/** Brand vocabulary editor (W2.7 frontend). */
import { useState } from 'react'

interface Props {
  vocabulary: Record<string, string>
  onChange: (next: Record<string, string>) => void
}

export function VocabularyEditor({ vocabulary, onChange }: Props) {
  const [src, setSrc] = useState('')
  const [dst, setDst] = useState('')

  const entries = Object.entries(vocabulary)

  const add = () => {
    const k = src.trim()
    const v = dst.trim()
    if (!k || !v) return
    onChange({ ...vocabulary, [k]: v })
    setSrc('')
    setDst('')
  }

  const remove = (k: string) => {
    const next = { ...vocabulary }
    delete next[k]
    onChange(next)
  }

  return (
    <div data-testid="vocabulary-editor" className="space-y-3">
      <div className="flex gap-2">
        <input
          value={src}
          onChange={(e) => setSrc(e.target.value)}
          placeholder="Source word"
          className="flex-1 bg-zinc-900 border border-white/10 rounded px-2 py-1 text-xs"
        />
        <input
          value={dst}
          onChange={(e) => setDst(e.target.value)}
          placeholder="Replacement"
          className="flex-1 bg-zinc-900 border border-white/10 rounded px-2 py-1 text-xs"
        />
        <button
          type="button"
          data-testid="vocab-add"
          onClick={add}
          disabled={!src.trim() || !dst.trim()}
          className="text-xs px-2 py-1 rounded border border-white/20 hover:border-white/40 disabled:opacity-50"
        >
          Add
        </button>
      </div>
      {entries.length === 0 ? (
        <p className="text-[11px] text-zinc-500">
          No replacements. Captions render unchanged.
        </p>
      ) : (
        <ul className="space-y-1">
          {entries.map(([k, v]) => (
            <li
              key={k}
              data-testid={`vocab-entry-${k}`}
              className="flex items-center justify-between text-xs gap-3"
            >
              <span className="text-zinc-300 font-mono">
                {k} → {v}
              </span>
              <button
                type="button"
                onClick={() => remove(k)}
                className="text-zinc-500 hover:text-red-400"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
