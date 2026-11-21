// Collect files from a drop event, including dropped FOLDERS (recursive), keeping only supported formats.
import { allowedFile } from './format'

type Entry = FileSystemEntry & { isFile: boolean; isDirectory: boolean }

function readEntry(entry: Entry, out: File[], skipped: string[]): Promise<void> {
  return new Promise((resolve) => {
    if (entry.isFile) {
      (entry as FileSystemFileEntry).file((f) => {
        if (allowedFile(f.name)) out.push(f)
        else skipped.push(f.name)
        resolve()
      }, () => resolve())
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader()
      const all: Entry[] = []
      const next = () => reader.readEntries(async (batch) => {
        if (!batch.length) {
          for (const e of all) await readEntry(e, out, skipped)
          resolve()
        } else {
          all.push(...(batch as Entry[]))
          next()
        }
      }, () => resolve())
      next()
    } else resolve()
  })
}

export async function filesFromDrop(dt: DataTransfer): Promise<{ files: File[]; skipped: string[] }> {
  const files: File[] = []
  const skipped: string[] = []
  const items = Array.from(dt.items || [])
  const entries = items.map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean) as Entry[]
  if (entries.length) {
    for (const e of entries) await readEntry(e, files, skipped)
  } else {
    const r = filterFiles(dt.files)
    files.push(...r.files)
    skipped.push(...r.skipped)
  }
  return { files, skipped }
}

export function filterFiles(list: FileList | File[]): { files: File[]; skipped: string[] } {
  const files: File[] = []
  const skipped: string[] = []
  for (const f of Array.from(list)) (allowedFile(f.name) ? files.push(f) : skipped.push(f.name))
  return { files, skipped }
}
