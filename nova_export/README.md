# NOVA annotations → dataset release

The video annotations were made in NOVA (database `uncertainty-roleplay` on
`nova.hcai.eu`). Each NOVA session is one role-play scene, named
`Group<N>_<scene>` (e.g. `Group1_1` = Group 1, scene 1). Each annotation tier
(e.g. *Both*, *Off topic*, *Robot*, *User*, *Positive affect*,
*Leading_interaction_robot*, *Leading_interaction_user*) is a list of
time-aligned segments: `from`, `to` (seconds from the start of
`roleplay.video.mp4`), `label`, `confidence`.

`merge_nova_annotations.py` collects all tiers of all sessions and writes:

| File | Content |
|---|---|
| `nova_annotations.json` | standoff file, one entry per session with all its tiers (recommended for the release) |
| `nova_annotations.csv` | same data, one row per segment (easy for statistics / inter-annotator agreement) |
| `merged/Group<N>.json` | the dialogue JSON with `nova_session` and `nova_annotations` added to each scene |

Scenes are matched by group number and scene number. The dialogue turns have no
timestamps, so the annotations are attached at scene level, not to individual
turns.

## Step 1: get the annotations out of NOVA

### Option A: straight from the database (all sessions at once)

Run on a machine that can reach the NOVA server, with your NOVA login:

```bash
pip install pymongo
python nova_export/merge_nova_annotations.py \
    --mongo "mongodb://USER:PASSWORD@nova.hcai.eu:37317" \
    --db uncertainty-roleplay \
    --annotators shenshuy \
    --dialogues json_data --out nova_release
```

Leave out `--annotators` to export every annotator (useful for agreement).

### Option B: from files exported in the NOVA GUI

For each session: `DATABASE > Load` the session's annotations, then save each
tier to disk via the `FILE` menu (save/export annotation). NOVA writes a pair
of files per tier:

```
roleplay.Both.shenshuy.annotation    # XML header with the label names
roleplay.Both.shenshuy.annotation~   # data: from;to;label_id;confidence
```

Save them as ASCII (not binary). A CSV export per tier (`from;to;label;confidence`)
also works. Put them in one folder per session:

```
nova_files/
  Group1_1/  roleplay.Both.shenshuy.annotation(~)  roleplay.Robot.shenshuy.annotation(~) ...
  Group1_2/  ...
```

Then:

```bash
python nova_export/merge_nova_annotations.py --files nova_files \
    --dialogues json_data --out nova_release
```

The script prints which dialogue scenes have no NOVA session and which NOVA
sessions have no dialogue scene, so mismatched names show up immediately.

## Output format

```json
{
  "session": "Group1_1", "group": 1, "scene": 1,
  "tiers": [
    {
      "scheme": "Both", "type": "DISCRETE", "role": "roleplay", "annotator": "shenshuy",
      "segments": [
        {"from": 0.16, "to": 16.44, "label": "Grounded", "confidence": 1.0},
        {"from": 32.5, "to": 81.2, "label": "Not Grounded", "confidence": 1.0}
      ]
    }
  ]
}
```

Unlabelled (NOVA "garbage") segments get `"label": null`.

## Labels per turn (`align_turns.py`)

The dialogue turns have no timestamps, so `align_turns.py` first force-aligns
the existing transcription to each session's video with the MMS aligner from
torchaudio (runs on CPU, no re-transcription), then adds `start`, `end` and
the overlapping NOVA labels to every turn:

```json
{"speaker": "Robot", "text": "...", "annotation": "Inform",
 "start": 47.9, "end": 52.3, "align_score": 0.91,
 "nova": {"Robot": ["Pausing"], "Both": ["Not Grounded"], "Off topic": ["On topic"]}}
```

```bash
pip install "torch==2.8.*" "torchaudio==2.8.*" num2words   # plus ffmpeg on PATH
python nova_export/align_turns.py --dialogues json_data --media nova_media \
    --nova-files nova_files --out nova_release
```

`nova_media/` holds one video per session (`Group1_1/roleplay.video.mp4` or
`Group1_1.mp4`). Check the result by opening `nova_release/srt/Group1_1.srt`
as subtitles next to the video in VLC. Turns with a low `align_score` are
listed at the end of the run.
