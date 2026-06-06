# uk-evidence-mcp

An MCP server that gives Claude access to **UK-accessible** clinical evidence:
**NICE guidance**, **NICE Clinical Knowledge Summaries (CKS)**, and **PubMed**.

It exists because OpenEvidence withdrew from the UK and EU in 2026 (citing the
EU AI Act), which makes the OpenEvidence-based MCP servers useless here. This
covers the same point-of-care-plus-evidence need with sources a UK GP can
actually reach. All sources are public; **no API key is required**.

## What it does

Seven tools, in two layers — find, then read the full text:

| Tool | Purpose |
|------|---------|
| `nice_search(query, max_results)` | Find NICE guidelines / quality standards / TAs. Returns titles, reference codes (NG196), abstracts, URLs. |
| `nice_guidance(reference, chapter)` | Full text of a NICE guidance chapter. Defaults to the Recommendations chapter and lists the others. |
| `cks_search(query, max_results)` | Find CKS topics (the concise primary-care summaries) by name. |
| `cks_topic(topic)` | A CKS topic's summary plus its section map. |
| `cks_section(topic, section)` | Full text of one CKS section, e.g. `management` or `prescribing-information/colchicine`. Thin hub pages auto-expand into their sub-sections. |
| `mhra_search(query, max_results, kind)` | Search MHRA Drug Safety Updates and medicine/device safety alerts. `kind` = `dsu`, `alert`, or `all`. |
| `mhra_article(path)` | Full text of one MHRA Drug Safety Update or safety alert. |
| `pubmed_search(query, max_results)` | Search PubMed (supports PubMed syntax). Returns title, authors, journal, year, PMID, publication type. |
| `pubmed_abstract(pmid)` | Full abstract(s) for one or more PMIDs. |

## Install

Python 3.10+.

```bash
cd uk-evidence-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

(Or install into your system Python: `pip install -r requirements.txt`.)

Quick check it runs:

```bash
python server.py        # should sit silently waiting on stdin — that's correct; Ctrl-C to exit
```

## Wire it into Claude Desktop

Edit (create if missing):
`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "uk-evidence": {
      "command": "/ABSOLUTE/PATH/TO/uk-evidence-mcp/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/uk-evidence-mcp/server.py"]
    }
  }
}
```

Use absolute paths. If you installed into system Python rather than a venv, set
`"command": "python3"` instead. Restart Claude Desktop; you should see the seven
tools appear under the connectors / tools menu.

## Wire it into Claude Code

```bash
claude mcp add uk-evidence -- /ABSOLUTE/PATH/TO/uk-evidence-mcp/.venv/bin/python /ABSOLUTE/PATH/TO/uk-evidence-mcp/server.py
```

Then `/mcp` inside Claude Code to confirm it connected.

## Example session

> "What does NICE say about anticoagulation in atrial fibrillation, and is
> there a Cochrane review on apixaban vs warfarin?"

Claude would call `nice_search("atrial fibrillation anticoagulation")`, then
`nice_guidance("NG196")` for the recommendations, then `pubmed_search("apixaban
warfarin atrial fibrillation systematic review[pt]")` and `pubmed_abstract(...)`.

## Notes, limits and good behaviour

- **Not a medical device.** It retrieves and quotes published guidance; it does
  not give advice. Clinical judgement stays with the clinician.
- **NICE/CKS have no open API**, so the server reads their public web pages and
  extracts the text. If NICE restructures those pages the parsers may need a
  tweak — the failure mode is a clear error message, not silent nonsense.
- **CKS content** is © Clarity Informatics (Agilio Software Primary Care) and
  governed by the CKS End User Licence Agreement; this tool is for your own
  point-of-care reference, the same use the website intends.
- **PubMed** uses NCBI E-utilities, which ask you to identify yourself — the
  server sends a tool name and the contact email set at the top of `server.py`.
  Heavy use should add an NCBI API key (raises the rate limit from 3 to 10
  requests/sec); it works fine without one for interactive use.
- **MHRA** data comes from the fully open gov.uk search and content APIs — no
  key, no scraping.
- **Possible v3 additions:** BNF dosing (no API — would need careful scraping)
  and a SNOMED lookup (cf. the `pacharanero/sct` tooling). Say the word.

## Why this shape

The two-layer design (search → full text) is deliberate: it lets Claude ground
its reasoning in the actual recommendation text rather than a snippet, which is
exactly the "evidence-grounded second call" pattern the Diagnostic Teammate
wants. A NICE/CKS retrieval tool is the UK-legal substitute for the
OpenEvidence call.
