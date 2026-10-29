import { Label } from '@/components/ui/label'

/** Same cap as the API (`CreateJobRequest.prompt`, the upload form, the reprompt). */
export const PROMPT_MAX_LENGTH = 2000

export const SCENE_PROMPT_HELP =
  'Describe what to find, by what is seen or said. With AI scene search set up on the server ' +
  '(YTVIDEO_SCENE_SEARCH_PROVIDER), the prompt picks the clips and overrides the video’s chapters; ' +
  'otherwise it ranks the discovered clips by their transcript.'

interface Props {
  id: string
  value: string
  onChange: (value: string) => void
}

/** Optional "what to find" prompt for a new job or upload (spec 014). */
export function ScenePromptField({ id, value, onChange }: Props) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>Find moments (optional)</Label>
      <textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={PROMPT_MAX_LENGTH}
        rows={2}
        placeholder="e.g. every shot of a red car, or the moment the speaker gets emotional"
        aria-describedby={`${id}-help`}
        className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500"
      />
      <p id={`${id}-help`} className="text-xs text-zinc-500">
        {SCENE_PROMPT_HELP}
      </p>
    </div>
  )
}
