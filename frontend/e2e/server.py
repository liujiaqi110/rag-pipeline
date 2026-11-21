"""End-to-end test server: the REAL backend (FastAPI + Alembic SQLite + background job thread + real BM25 +
real bge-m3 Dense if available + real local Ollama qwen2.5:7b) serving the BUILT frontend (frontend/dist).

Started by Playwright (playwright.config.ts → webServer). Everything lives in a fresh directory under build/e2e/
(git-ignored). Scenario documents are generated here — a fictional company "星河制造", synthetic content only.

    KB_E2E_ADMIN_PASSWORD=... python frontend/e2e/server.py
"""

import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RUN = ROOT / "build" / "e2e" / time.strftime("run-%Y%m%d-%H%M%S")
RUN.mkdir(parents=True, exist_ok=False)
FILES = RUN / "files"
FILES.mkdir()

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.update(KB_DATABASE_URL=f"sqlite:///{RUN / 'kb.db'}", KB_STORAGE_DIR=str(RUN / "storage"),
                  KB_JOBS_MODE="thread", KB_TOKEN_FORMAT="jwt", KB_RETRIEVAL_MODE=os.environ.get("KB_RETRIEVAL_MODE", "rrf"),
                  KB_DENSE=os.environ.get("KB_DENSE", "auto"), KB_ANSWER_CACHE_TTL_S="300")

from kb_platform.tests.ingest_fixtures import pdf_doc  # noqa: E402


def md_release(c):
    return f"""# 研发发布规范

本文是星河制造（虚构公司）研发部的合成示例文档。标记 {c}。

## 发布前检查

研发部每次发布前必须完成三项检查：自动化测试全部通过、变更说明经过同行评审、回滚脚本在预发环境演练成功。缺少任何一项都不得进入发布窗口。标记 {c}。

## 发布窗口

常规发布窗口为每周二和周四下午两点到四点。紧急修复需要值班负责人批准，并在二十四小时内补写复盘报告。
""".encode("utf-8")


def txt_holiday(c):
    return (f"星河制造公司假期安排（虚构）\n\n年假按工龄计算，入职满一年享有五天年假，满五年享有十天。标记 {c}。\n\n"
            f"请假需要提前三个工作日在系统中提交，由直属主管审批。春节假期统一安排为七天。{c}\n").encode("utf-8")


def docx_salary(c):
    import docx
    d = docx.Document()
    d.add_heading("薪酬管理制度", level=1)
    d.add_paragraph(f"本文是星河制造（虚构）人力资源部的机密合成文档，标记 {c}。薪酬分为 P1 至 P6 六个等级，"
                    "P3 的年度薪酬区间为虚构数值二十万到二十八万。调薪每年三月进行，需要人力资源负责人审批。")
    d.add_heading("保密要求", level=2)
    d.add_paragraph(f"薪酬数据只允许在人力资源部内部查阅，不得通过即时通讯工具传播。{c} 违反保密要求将按制度处理。"
                    "所有薪酬表格每季度归档一次，归档前必须完成保密检查。")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def pdf_quality(c):
    l1 = "The quality audit checks incoming material inspection records, calibration certificates and defect trends."
    l2 = "Corrective actions must be closed within thirty days and verified by the quality auditor on site."
    return pdf_doc([["Xinghe Manufacturing Quality Audit Report (fictional)", f"Marker {c} page one.", l1, l1],
                    ["Corrective Actions", f"Marker {c} page two.", l2, l2]])


docs = {
    "研发发布规范.md": md_release("XH-RND-7731"),
    "薪酬管理制度.docx": docx_salary("XH-HR-5520"),
    "公司假期安排.txt": txt_holiday("XH-HOLIDAY-V1"),
    "Quality_Audit_Report.pdf": pdf_quality("XH-QA-3141"),
}
for name, data in docs.items():
    (FILES / name).write_bytes(data)
(FILES / "v2").mkdir()
(FILES / "v2" / "公司假期安排.txt").write_bytes(txt_holiday("XH-HOLIDAY-V2"))

from sqlalchemy import select  # noqa: E402
from kb_platform import security  # noqa: E402
from kb_platform.app import create_app  # noqa: E402
from kb_platform.models import User  # noqa: E402

app = create_app()
pw = os.environ["KB_E2E_ADMIN_PASSWORD"]
with app.state.sessionmaker() as db:
    if not db.scalar(select(User).where(User.username == "e2e_admin")):
        db.add(User(username="e2e_admin", display_name="平台管理员", password_hash=security.hash_password(pw), is_admin=True))
        db.commit()

info = {"run_dir": str(RUN), "files": {k: str(FILES / k) for k in docs}, "v2_holiday": str(FILES / "v2" / "公司假期安排.txt")}
out = ROOT / "frontend" / ".e2e"
out.mkdir(exist_ok=True)
(out / "run.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("KB_E2E_PORT", "8765")), log_level="warning")
