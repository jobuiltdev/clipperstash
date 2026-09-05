# Evaluation data

Local working directory for detector calibration. Put label files, experimental
configurations and generated reports here.

## Nothing real in this directory is ever committed

The `.gitignore` alongside this file ignores everything except itself, this
README and `examples/`. That is deliberate, and it should stay that way.

A label file names a real broadcast and the exact seconds a real audience
reacted. A generated report describes that audience's behaviour second by second.
Neither contains chat text or chatter identity — the formats have no field for
either — but both are derived from real people and belong on the operator's
machine, not in a public repository.

If you add a new kind of evaluation artifact, keep it under this directory so
the existing ignore rule covers it.

## Files

| Path | What it is |
| --- | --- |
| `examples/labels.example.json` | A ground-truth label file. Synthetic. |
| `examples/config.example.json` | An experimental detector configuration. Synthetic. |
| `session-<id>.json` | Your own labels for a session. Ignored. |
| `report-<id>-<date>.json` | A generated report. Ignored. |

The two examples use invented timestamps and a session id that will not exist in
your database. They are here to show the shape of each file; copy one and edit
it rather than writing a file from scratch.

## Creating labels

Watch the stream, or its VOD, and note when something genuinely worth clipping
happened. Each label is one instant plus a tolerance — how far from that instant
a detector candidate may land and still be the same event.

```json
{
  "version": 1,
  "session_id": 12,
  "default_tolerance_seconds": 10,
  "moments": [
    { "timestamp": "2026-09-05T18:30:15Z", "note": "clutch 1v4" },
    { "timestamp": "2026-09-05T19:02:48Z", "tolerance_seconds": 20 }
  ]
}
```

`note` is optional and for your own memory; nothing reads it. `tolerance_seconds`
on a moment overrides `default_tolerance_seconds` for that moment alone.

Label honestly, including the quiet stretches you would not have clipped. A file
containing only the moments the detector already found measures nothing.

## Running an evaluation

```bash
python manage.py evaluate_moments 12 --labels evaluation_data/session-12.json
```

Add `--compare evaluation_data/examples/config.example.json` to see an experiment
next to the baseline, and `--output evaluation_data/report-12.json` to keep the
numbers.

Evaluation reads chat and writes nothing. It never contacts Twitch and never
creates a clip.

See the Detector evaluation section of the project README for the full workflow.
