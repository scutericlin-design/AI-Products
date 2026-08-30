# NotebookLM Sync Automation

## What This Automates

Goal:

```text
Notion Research Pack / selected article
-> NotebookLM Source Pack
-> NotebookLM copied-text source
-> conclusions written back to Notion
```

NotebookLM does not currently expose a stable public API for creating notebooks
or adding sources. The durable part of the automation is therefore the export
and Google Drive layer. The final import step is handled through the browser
using your logged-in Google account.

## One-Time Notion Upgrade

Add sync fields to the Notion databases:

```bash
python3 knowledge-base-stack/scripts/upgrade_notion_notebooklm_sync.py \
  --token-file /private/tmp/notion_api_key.txt
```

This adds:

- `NotebookLM Sync`
- `NotebookLM Sync Status`
- `NotebookLM Source Pack Path`
- `NotebookLM Last Exported`
- `NotebookLM Export Hash`

## Export A Research Pack

List recent Research Packs:

```bash
python3 knowledge-base-stack/scripts/export_notion_to_notebooklm_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --list
```

Export a specific pack:

```bash
python3 knowledge-base-stack/scripts/export_notion_to_notebooklm_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --research-pack-title "2026-06 Knowledge Stack - Notion Onyx NotebookLM" \
  --update-notion
```

Use a temporary token file rather than putting the token directly in the shell
command. Do not commit the token file.

Output goes to:

```text
knowledge-base-stack/notebooklm-source-packs/
```

Each export creates:

- `.md`: readable source pack
- `.txt`: best file for NotebookLM copied-text import
- `.json`: manifest with paths, source page, and hash

## Import To NotebookLM

Preferred path: use a Google Drive Doc source.

```bash
node knowledge-base-stack/scripts/notebooklm_local_upload.js --method auto
```

With `--method auto`, queued items that already have a Drive Doc are attached
as Google Drive sources. Items without a Drive Doc fall back to NotebookLM's
`Copied text` source type:

1. Open the target NotebookLM notebook.
2. Click `Add source`.
3. Choose `Copied text`.
4. Paste the generated `.txt` file content.
5. Name the source with the Research Pack title.

When I operate the browser for you, this is the step I automate.

## Mark Import Complete In Notion

After the `.txt` source is added to NotebookLM, mark the Research Pack as
imported:

```bash
python3 knowledge-base-stack/scripts/update_notion_research_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --database-id 38c35c1d-9fdc-8196-a44a-d26ed503bcfa \
  --title "2026-06 Knowledge Stack - Notion Onyx NotebookLM" \
  --notebooklm-url https://notebooklm.google.com/notebook/2ca0cfe1-7f5d-4509-b195-81622ff407bb \
  --notebooklm-sync-status Added \
  --notebooklm-source-pack-path "knowledge-base-stack/notebooklm-source-packs/2026-06-knowledge-stack-notion-onyx-notebooklm-ec168ad088fbfdd5.txt" \
  --notebooklm-last-exported
```

## Operating Rules

- One NotebookLM notebook per serious Research Pack.
- One Source Pack per export version.
- Do not pipe the entire Notion workspace into NotebookLM.
- Always write useful NotebookLM outputs back to Notion.
- If a Source Pack changes materially, export a new version instead of trying to silently mutate the old source.

## Current Limitation

NotebookLM import is browser-driven because there is no stable public NotebookLM API for source creation. If Google changes the UI, this step may need a small browser automation adjustment. The Notion export side is API-based and stable.
