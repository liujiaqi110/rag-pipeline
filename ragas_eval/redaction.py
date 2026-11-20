"""Configurable redaction of sensitive values in Markdown corpora (P0-4). Offline, 0 API calls.

  python -m ragas_eval.redaction --input-dir experiments/clean_text_v07/clean_md \
      --output-dir experiments/redaction_v07/clean_md --log experiments/redaction_v07/redaction_log.json
      [--enable windows_user_path] [--enable invite_code]

Design
  * Structured, semantic placeholders: `ssh -p <SSH_PORT> <SSH_USER>@<SSH_HOST>`, `端口号 : <SSH_PORT>` …
  * SSH field rules only fire inside SSH context (the text mentions ssh), so ordinary "用户名:" examples survive.
  * Allowlisted example / local hosts (localhost, 127.0.0.1, 0.0.0.0, example.com/org/net, *.local, *.test)
    and version numbers are never touched.
  * v0.7b (P0-4b): single-@ ``user@host`` inside an SSH window (+-4 lines mentioning ssh/连接串/主机名…), ssh commands
    with or without ``-p`` incl. targets wrapped onto the next line by the PDF parser, and a corpus-level consistency
    pass: values confirmed by high-confidence forms (ssh targets, 用户名/主机名 fields, instance prompts, user-dir
    names) are replaced in every other context too. Mailbox-looking addresses next to SSH text -> needs_review
    (kept, logged). Ordinary e-mail addresses outside SSH windows are never touched; @ count is never the criterion.
  * Optional rules (off by default): local Windows username in paths, marketing referral codes.
  * The log records rule, file, line number, column and replacement token ONLY — never the original value,
    never a hash of it (short values such as ports would be brute-forceable), never its length.
  * Output directory must be outside the official corpus directories (parsed_md/, clean_md/, chunks/, index/, raw_docs/).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import PROJECT_ROOT

PROTECTED_DIRS = [PROJECT_ROOT / d for d in ("raw_docs", "parsed_md", "clean_md", "chunks", "index")]
ALLOWED_HOSTS = re.compile(r"^(?:localhost|127\.0\.0\.1|0\.0\.0\.0|::1|(?:[\w-]+\.)*example\.(?:com|org|net)|[\w.-]+\.(?:local|test|invalid))$", re.I)
SSH_CONTEXT = re.compile(r"\bssh\b", re.I)


@dataclass(frozen=True)
class Rule:
    name: str
    category: str          # connection_parameter | credential | personal_identifier
    pattern: re.Pattern
    replace: Callable[[re.Match], Optional[str]]  # returns replacement or None to keep (allowlisted)
    needs_ssh_context: bool = False
    default_enabled: bool = True


def _host_ok(host: str) -> bool:
    return bool(ALLOWED_HOSTS.match(host.strip(".:")))


# --- v0.7b (P0-4b) ------------------------------------------------------------------------------------
# A connection string may be wrapped by the PDF parser: "ssh -p 1234 user@root@ssh-" / "<blank>" / "abc.def@host.com".
WRAP_CONT = r"(?:(?<=[\-.@])[ \t]*\n(?:[ \t]*\n){0,2}[ \t]*[\w.\-]+(?:@[\w.\-]+)*(?=[ \t]*(?:\n|$)))?"
HOSTLIKE = r"(?:[A-Za-z0-9](?:[\w\-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}"
# words that put a line into "SSH context" (window of +-WINDOW lines)
SSH_WINDOW_WORDS = re.compile(r"\bssh\b|连接串|远程连接|远程开发|主机名|端口号", re.I)
WINDOW = 4
MAIL_DOMAINS = re.compile(r"^(?:gmail|googlemail|outlook|hotmail|live|yahoo|icloud|qq|foxmail|163|126|sina|sohu|aliyun|proton|protonmail)\.",
                          re.I)
GENERIC_PARTS = {"root", "ssh", "admin", "user", "ubuntu", "centos", "debian", "git", "dev", "test", "demo", "ec2-user",
                 "your_name", "username", "host", "hostname"}
PLACEHOLDER = re.compile(r"<[A-Z_]+>")


def _ssh_cmd(m: re.Match) -> Optional[str]:
    target = re.sub(r"\s+", "", m.group("target"))
    host = target.rsplit("@", 1)[-1]
    return None if _host_ok(host) else f"{m.group('pre')}<SSH_PORT> <SSH_USER>@<SSH_HOST>"


def _ssh_generic(m: re.Match) -> Optional[str]:
    target = re.sub(r"\s+", "", m.group("target"))
    host = target.rsplit("@", 1)[-1]
    if _host_ok(host) or PLACEHOLDER.search(target):
        return None
    return f"{m.group('pre')}<SSH_USER>@<SSH_HOST>"


def _multi_at(m: re.Match) -> Optional[str]:
    return None if _host_ok(m.group(0).rsplit("@", 1)[-1]) else "<SSH_USER>@<SSH_HOST>"


def _field(token: str) -> Callable[[re.Match], Optional[str]]:
    def f(m: re.Match) -> Optional[str]:
        val = m.group("val")
        if val.startswith("<") or _host_ok(val) or re.fullmatch(r"(?:x+|your\w*|<.*>|示例\w*|你的\w*)", val, re.I):
            return None
        return m.group("lab") + token
    return f


def _prompt(m: re.Match) -> Optional[str]:
    return None if _host_ok(m.group("host")) else f"{m.group('user')}@<INSTANCE_HOST>"


def _password(m: re.Match) -> Optional[str]:
    val = m.group("val")
    if re.match(r"(?:如果|请|需要|要|自己|设置|输入)", val) or val.startswith("<"):
        return None  # descriptive text, not a value
    return m.group("lab") + "<PASSWORD>"


REVIEW = "__needs_review__"  # sentinel: keep text, emit a needs_review event


def _single_at(m: re.Match) -> Optional[str]:
    """Single-@ user@host. Only called for matches inside an SSH window (see redact_text)."""
    host = m.group("host")
    if _host_ok(host):
        return None
    if MAIL_DOMAINS.match(host):
        return REVIEW  # looks like a mailbox even though it sits next to SSH text: a human decides
    return "<SSH_USER>@<SSH_HOST>"


RULES: List[Rule] = [
    Rule("ssh_command", "connection_parameter",
         re.compile(r"(?P<pre>\bssh\s+-p\s*)\d{1,5}\s+(?P<target>[\w.\-]+(?:@[\w.\-]*)+" + WRAP_CONT + ")", re.I), _ssh_cmd),
    Rule("ssh_command_target", "connection_parameter",
         re.compile(r"(?P<pre>\bssh\s+(?:-[A-Za-z]+(?:[ \t]+(?![\w.\-]+@)[^\s@]+)?[ \t]+)*)(?P<target>(?<![<\w])[\w.\-]+(?:@[\w.\-]*)+"
                    + WRAP_CONT + ")", re.I), _ssh_generic),
    Rule("ssh_user_host", "connection_parameter",
         re.compile(r"(?<![\w<])[\w.\-]+@[\w.\-]+@[\w.\-]+(?:@[\w.\-]+)*"), _multi_at, needs_ssh_context=True),
    Rule("ssh_port_field", "connection_parameter",
         re.compile(r"(?P<lab>端口号?\s*[:：]\s*)(?P<val>\d{2,5})\b"), _field("<SSH_PORT>"), needs_ssh_context=True),
    Rule("ssh_username_field", "connection_parameter",
         re.compile(r"(?P<lab>用户名\s*[:：]\s*)(?P<val>[\w.\-@]{3,})"), _field("<SSH_USER>"), needs_ssh_context=True),
    Rule("ssh_hostname_field", "connection_parameter",
         re.compile(r"(?P<lab>主机名\s*[:：]\s*)(?P<val>[\w.\-]{3,})"), _field("<SSH_HOST>"), needs_ssh_context=True),
    Rule("shell_prompt_instance_host", "connection_parameter",
         re.compile(r"\b(?P<user>root|[a-z_][\w-]{0,31})@(?P<host>[\w.-]*\d{6,}[\w.-]*)(?=\s*:)"), _prompt),
    Rule("ssh_single_at_in_ssh_window", "connection_parameter",
         re.compile(r"(?<![\w<@.\-])(?P<user>[\w.\-]+)@(?P<host>" + HOSTLIKE + r")(?![\w@\-])"), _single_at,
         needs_ssh_context=True),
    Rule("password_value", "credential",
         re.compile(r"(?P<lab>(?:密码|口令|password|passwd)\s*[:：=]\s*)(?P<val>[^\s，。,;；]{1,64})", re.I), _password),
    Rule("windows_user_path", "personal_identifier",
         re.compile(r"(?P<pre>[A-Za-z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/))(?P<user>[^\\/\s\"'<>]+)"),
         lambda m: None if m.group("user").lower() in ("public", "default", "<local_user>") else m.group("pre") + "<LOCAL_USER>",
         default_enabled=False),
    Rule("invite_code", "personal_identifier",
         # v0.8.2: the value may be split by whitespace in markdown link text ("...4373 b34e..."), so hex-looking
         # continuation tokens are consumed until the link/URL ends.
         re.compile(r"(?P<pre>inviteCode=)[\w-]+(?:[ \t]+[0-9A-Fa-f]{2,})*(?=[\])\s\"'&#]|$)", re.I),
         lambda m: m.group("pre") + "<INVITE_CODE>", default_enabled=False),
]
WINDOWED_RULES = {"ssh_single_at_in_ssh_window"}  # need an SSH word within +-WINDOW lines, not just in the file
# pseudo-rules (only appear in events): consistency replacement of confirmed values
CONSISTENCY_RULES = {"SSH_HOST": "confirmed_ssh_host", "SSH_USER": "confirmed_ssh_user",
                     "INSTANCE_HOST": "confirmed_instance_host", "LOCAL_USER": "windows_user_bare"}
RULE_NAMES = [r.name for r in RULES]


@dataclass
class RedactionConfig:
    enabled: Tuple[str, ...] = tuple(r.name for r in RULES if r.default_enabled)

    @classmethod
    def with_optional(cls, extra: List[str]) -> "RedactionConfig":
        unknown = [e for e in extra if e not in RULE_NAMES]
        if unknown:
            raise ValueError(f"unknown rules {unknown}; known: {RULE_NAMES}")
        return cls(tuple(dict.fromkeys(list(cls().enabled) + list(extra))))


# ------------------------------------------------------------------ confirmed values (memory only, never logged)

def _user_parts(user: str) -> List[str]:
    out = []
    if len(user) >= 6 and user.lower() not in GENERIC_PARTS:
        out.append(user)
    for part in user.split("@"):
        for cand in (part, re.sub(r"^[A-Za-z]+-", "", part)):
            if len(cand) >= 6 and cand.lower() not in GENERIC_PARTS and re.search(r"\d", cand):
                out.append(cand)
    return out


def collect_confirmed(texts: List[str], cfg: RedactionConfig = RedactionConfig()) -> Dict[str, str]:
    """value -> placeholder name, from high-confidence forms only. Kept in memory; never written anywhere."""
    found: Dict[str, str] = {}

    def add(v: str, kind: str):
        v = v.strip(".:-")
        if not v or v.startswith("<") or PLACEHOLDER.search(v) or _host_ok(v):
            return
        if kind == "SSH_HOST" and ("." not in v or len(v) < 4):
            return
        found.setdefault(v, kind)

    cmd = re.compile(r"\bssh\s+(?:-[A-Za-z]+(?:[ \t]+(?![\w.\-]+@)[^\s@]+)?[ \t]+)*(?P<target>[\w.\-]+(?:@[\w.\-]*)+" + WRAP_CONT + ")", re.I)
    for text in texts:
        if not SSH_CONTEXT.search(text):
            continue
        targets = [re.sub(r"\s+", "", m.group("target")) for m in cmd.finditer(text)]
        targets += [m.group(0) for m in re.finditer(r"(?<![\w<])[\w.\-]+@[\w.\-]+@[\w.\-]+(?:@[\w.\-]+)*", text)]
        for t in targets:
            user, _, host = t.rpartition("@")
            if _host_ok(host):
                continue  # documented example / local target: nothing confirmed
            if "." in host and not re.fullmatch(r"[\w\-]+", host):
                add(host, "SSH_HOST")
            else:  # no host part (e.g. a user-only field value)
                user = t
            for u in _user_parts(user):
                add(u, "SSH_USER")
        for m in re.finditer(r"用户名\s*[:：]\s*(?P<val>[\w.\-@]{3,})", text):
            for u in _user_parts(m.group("val")):
                add(u, "SSH_USER")
        for m in re.finditer(r"主机名\s*[:：]\s*(?P<val>[\w.\-]{3,})", text):
            add(m.group("val"), "SSH_HOST")
    for text in texts:
        for m in RULES[RULE_NAMES.index("shell_prompt_instance_host")].pattern.finditer(text):
            add(m.group("host"), "INSTANCE_HOST")
        if "windows_user_path" in cfg.enabled:
            for m in RULES[RULE_NAMES.index("windows_user_path")].pattern.finditer(text):
                u = m.group("user")
                if len(u) >= 3 and u.lower() not in {"public", "default", "<local_user>"} | GENERIC_PARTS:
                    found.setdefault(u, "LOCAL_USER")
    return found


def _ssh_window_lines(text: str) -> List[bool]:
    lines = text.split("\n")
    hit = [bool(SSH_WINDOW_WORDS.search(l)) for l in lines]
    return [any(hit[max(0, i - WINDOW):i + WINDOW + 1]) for i in range(len(lines))]


def _event(rule, category, source, text, start, rep):
    return {"rule": rule, "category": category, "source": source, "line": text.count("\n", 0, start) + 1,
            "column": start - (text.rfind("\n", 0, start) + 1), "replacement": rep}


def redact_text(text: str, cfg: RedactionConfig = RedactionConfig(), source: str = "",
                confirmed: Optional[Dict[str, str]] = None) -> Tuple[str, List[Dict[str, Any]]]:
    """Return (redacted_text, events). Events carry no original values.

    ``confirmed`` (value -> placeholder) comes from :func:`collect_confirmed` over the whole corpus so the same
    host / user is replaced consistently in every context; when omitted it is collected from ``text`` itself.
    """
    if confirmed is None:
        confirmed = collect_confirmed([text], cfg)
    events: List[Dict[str, Any]] = []
    ssh_ctx = bool(SSH_CONTEXT.search(text))
    for rule in RULES:
        if rule.name not in cfg.enabled or (rule.needs_ssh_context and not ssh_ctx and rule.name not in WINDOWED_RULES):
            continue
        window = _ssh_window_lines(text) if rule.name in WINDOWED_RULES else None
        out, pos = [], 0
        for m in rule.pattern.finditer(text):
            if window is not None and not window[text.count("\n", 0, m.start())]:
                continue
            rep = rule.replace(m)
            if rep is None:
                continue
            if rep == REVIEW:
                events.append(_event(rule.name, "needs_review", source, text, m.start(), None))
                continue
            events.append(_event(rule.name, rule.category, source, text, m.start(), rep))
            out.append(text[pos:m.start()])
            out.append(rep)
            pos = m.end()
        out.append(text[pos:])
        text = "".join(out)
    # consistency pass: every remaining occurrence of a confirmed value, in any context
    for value, kind in sorted(confirmed.items(), key=lambda kv: -len(kv[0])):
        if kind == "LOCAL_USER" and "windows_user_path" not in cfg.enabled:
            continue
        rx = re.compile(r"(?<![\w.\-])" + re.escape(value) + r"(?![\w\-]|\.\w)")
        out, pos = [], 0
        for m in rx.finditer(text):
            rep = f"<{kind}>"
            events.append(_event(CONSISTENCY_RULES[kind], "personal_identifier" if kind == "LOCAL_USER" else "connection_parameter",
                                 source, text, m.start(), rep))
            out.append(text[pos:m.start()])
            out.append(rep)
            pos = m.end()
        out.append(text[pos:])
        text = "".join(out)
    return text, events


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def display_path(path: Path) -> str:
    """Path for logs: project-relative when possible; otherwise the user-directory segment is replaced
    (a temp dir under C:\\Users\\<name>\\ would otherwise leak the local user name into the log)."""
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return re.sub(r"([A-Za-z]:[\\/]+Users[\\/]+)[^\\/]+", r"\1<LOCAL_USER>", str(Path(path).resolve()))


def check_output_dir(out: Path) -> None:
    for d in PROTECTED_DIRS:
        if _inside(out, d) or _inside(d, out):
            raise ValueError(f"refusing to write redacted output into/over a protected corpus directory: {d.name}/")


def redact_dir(input_dir: Path, output_dir: Path, cfg: RedactionConfig) -> Dict[str, Any]:
    check_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    events: List[Dict[str, Any]] = []
    files = sorted(Path(input_dir).glob("*.md"))
    texts = {p: p.read_text(encoding="utf-8") for p in files}
    confirmed = collect_confirmed(list(texts.values()), cfg)  # corpus-level, in memory only
    for p in files:
        new, ev = redact_text(texts[p], cfg, source=p.name, confirmed=confirmed)
        (output_dir / p.name).write_text(new, encoding="utf-8")
        events += ev
    summary: Dict[str, int] = {}
    for e in events:
        summary[e["rule"]] = summary.get(e["rule"], 0) + 1
    return {"input_dir": display_path(input_dir), "output_dir": display_path(output_dir), "files": len(files),
            "enabled_rules": list(cfg.enabled), "summary": summary,
            "confirmed_value_kinds": dict(Counter(confirmed.values())),  # counts per kind only, no values
            "needs_review": sum(1 for e in events if e["category"] == "needs_review"), "events": events}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m ragas_eval.redaction", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--enable", action="append", default=[], help=f"optional rules: {[r.name for r in RULES if not r.default_enabled]}")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    res = redact_dir(Path(a.input_dir), Path(a.output_dir), RedactionConfig.with_optional(a.enable))
    Path(a.log).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"redacted {res['files']} files: {res['summary']}  log: {a.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
