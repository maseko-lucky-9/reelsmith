# Contract: `GET /jobs/{job_id}/source`

**Spec**: FR-001, FR-002 (and FR-003 for the shared range rules) | **Route**: `app/routers/media.py` | **Proving test**: `tests/contract/test_job_source_route.py`, `tests/unit/test_http_range.py`

The job's source video, read-only, for the editor's clean preview. Also answers at `/api/jobs/{job_id}/source` (`ApiPrefixMiddleware`, ADR-005). Access control is the app-level `require_api_key` dependency (with `YTVIDEO_REQUIRE_AUTH=true`: a bearer key or `?token=`), like every other route.

## Request

| Part | Value |
|---|---|
| Path | `job_id`: the job's id |
| `Range` header (optional) | one range `bytes=<start>-<end?>`; anything that does not match `^bytes=(\d+)-(\d*)$` (suffix ranges, several ranges, other units) is ignored and the whole file is sent, as the clip route does today |

## Responses

| Case | Status | Headers | Body |
|---|---|---|---|
| No `Range`, or a `Range` that does not match | 200 | `Content-Type` by suffix (below), `Accept-Ranges: bytes`, `Content-Length: <size>`, `Content-Disposition: inline; filename="<name>"` | the whole file |
| `Range: bytes=S-E` with `S < size` and `S <= E` (E clamped to `size - 1`; missing E means `size - 1`) | 206 | `Content-Range: bytes S-E/<size>`, `Accept-Ranges: bytes`, `Content-Length: E - S + 1`, `Content-Type`, `Content-Disposition: inline` | bytes S..E, streamed in 64 KiB chunks |
| `S >= size`, or `S > E` | 416 | `Content-Range: bytes */<size>` | `{"detail": "range not satisfiable"}` |
| Unknown job | 404 | | `{"detail": "job not found: <job_id>"}` (the jobs router's wording) |
| `video_path` empty (never recorded, or cleared by retention) | 404 | | `{"detail": "source video not retained"}` |
| `video_path` unsafe (NUL byte, relative, `..` part), resolves outside `retention.managed_roots()` (including through a symlink), or not a regular file | 404 | | `{"detail": "source video not retained"}`: one wording for every path failure, the path is never echoed |
| The job is being deleted (`orchestrator.deleting(job_id)`) | 409 | | `{"detail": "the job is being deleted"}` |
| Auth on, no or wrong key | 401 | | `{"detail": "Unauthorized"}` |

`Content-Type` by suffix (lower-cased): `.mp4`, `.m4v` `video/mp4`; `.mov` `video/quicktime`; `.webm` `video/webm`; `.mkv` `video/x-matroska`; anything else `application/octet-stream`.

## Shared range rules (`GET /clips/{clip_id}/video` too)

The range parsing and the 200/206/416 answers above are one helper used by both routes. The clip route keeps its `video/mp4` type and `attachment` disposition; its only behaviour change is the 416 (today a start past the end gives a 206 with a zero or negative `Content-Length`, `media.py:46-49`).

## Contract tests (each must fail when its rule is broken)

1. 200 with the whole body and `Accept-Ranges: bytes` without `Range`.
2. 206 `bytes=0-99`: body equals the file's first 100 bytes, `Content-Range: bytes 0-99/<size>`, `Content-Length: 100`.
3. 206 open-ended `bytes=<size-10>-`: the last 10 bytes.
4. 206 with E past the end: clamped to `size - 1`.
5. 416 for `bytes=<size>-` and for `bytes=50-10`, with `Content-Range: bytes */<size>`; the clip route answers the same 416.
6. 200 for `bytes=-500` (suffix range not supported: whole file).
7. 404 unknown job; 404 `video_path` None; 404 file missing; 404 path outside the download folder (a real file in `tmp_path` outside `YTVIDEO_DEFAULT_DOWNLOAD_PATH`); 404 for a symlink inside the download folder pointing outside it; 404 for a stored path with `..`, a relative path and a NUL byte; none of the 404 bodies contains the stored path.
8. 409 while the job is marked deleting.
9. `/api/jobs/{id}/source` answers like `/jobs/{id}/source`.
10. With auth required: 401 without a key, 206 with `?token=<key>`.
11. `Content-Type` `video/webm` for a `.webm` source, `application/octet-stream` for an unknown suffix.
