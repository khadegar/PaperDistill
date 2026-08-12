# Search and Access

## Contents

1. Source order
2. Database roles
3. Institutional access
4. Zotero workflow
5. Search logging
6. Full-text and version handling

## 1. Source order

Use the least fragile authoritative route for each operation:

1. Local project records and user-provided PDFs
2. Zotero local library and exports
3. The external production full-text index, when available
4. Open bibliographic APIs and official repositories
5. Database export functions
6. Authenticated browser interaction for subscription UI or full text
7. General web search for official metadata verification and legal open copies

Before browser automation, check whether an API, connector, export, or local file can complete the same semantic operation.

The external index is an added retrieval accelerator, not a replacement for any live web/database step. Continue the existing search strategy when currency, classic sources, target-journal coverage, or counter-evidence is required.

## 2. Database roles

| Source | Primary role | Notes |
|---|---|---|
| Web of Science | Multidisciplinary citation search | Use institutional export when available |
| Scopus | Broad engineering/biomedical coverage and citations | Export DOI, source title, abstract, keywords |
| PubMed/MEDLINE | Biomedical and clinical indexing | Prefer NCBI records and PMID linking |
| IEEE Xplore | Biomedical engineering, imaging, computation | Distinguish journal and conference papers |
| ScienceDirect/Elsevier | Publisher metadata and subscribed full text | Use DOI metadata before UI extraction |
| Google Scholar | Citation chaining and hard-to-find versions | Use conservatively; do not treat result counts as reproducible |
| Crossref | DOI and publisher metadata verification | Metadata source, not evidence content |
| OpenAlex | Discovery, concepts, cited-by links, OA locations | Verify important metadata against DOI/publisher |
| arXiv | Preprints | Link to a published version when one exists |
| Zotero | User-owned corpus, attachments, tags, collections | Deduplicate before frequency analysis |

Also use Engineering Village, Embase, ProQuest, CNKI, Wanfang, standards databases, and publisher archives when the question requires them and access exists.

## 3. Institutional access

Use the user's authenticated Chrome session for institution-only content.

- Let the user complete username, password, MFA, CAPTCHA, VPN, or proxy actions.
- Never inspect cookies, browser profiles, saved passwords, local storage, or session databases.
- Do not ask the user to paste account credentials into chat.
- Stop and request sign-in when the selected browser is unauthenticated.
- Respect database export and pagination limits; prefer RIS/BibTeX/CSV export over scraping result pages.
- Index query results and selected documents only. Do not attempt to clone an entire subscription database.

For each retrieved full text, record whether access came from a local file, institutional publisher page, open repository, author manuscript, or preprint.

## 4. Zotero workflow

When the Zotero plugin is available:

1. Run its status command before inventory or export.
2. Use `inventory`, `search`, collections, or BibTeX export for read-only analysis.
3. Do not retrieve attachment paths or full text unless requested.
4. Treat import, citation insertion, collection changes, and record edits as writes.
5. Explain that Zotero item keys and BibTeX citekeys are different identifiers.

For journal-frequency analysis:

1. Export a temporary BibTeX snapshot.
2. Exclude attachments, notes, books, and records without a journal field.
3. Deduplicate by DOI, then normalized title plus year.
4. Normalize journal title case, punctuation, ampersands, abbreviations, former titles, and successor titles.
5. Report both raw and deduplicated counts.
6. Optionally filter by domain keywords, tags, or a Zotero collection.
7. Delete the temporary export after analysis unless the user wants to keep it.

Use `scripts/zotero_journal_stats.py` for reproducible statistics from a BibTeX export.

## 5. Search logging

Append one row per executed query to `search/search-log.csv`:

```text
search_id,run_at,database,query,date_range,filters,result_count,export_file,notes
```

Record actual syntax, including field codes and filters. Log failed and zero-result searches because they inform reproducibility.

Maintain corpus flow counts:

```text
identified -> deduplicated -> title_abstract_screened -> full_text_assessed -> included
```

Never claim comprehensive coverage if key databases, languages, full texts, or citation chains were unavailable.

## 6. Full-text and version handling

Assign exactly one access level per analysis pass:

- `metadata_only`: title/author/journal/identifier only
- `abstract_read`: abstract and metadata read
- `full_text_read`: main article read with page/section locators
- `supplement_read`: main article and relevant supplementary material read

Track version relationships:

- preprint to version of record
- accepted manuscript to publisher version
- conference paper to expanded journal paper
- correction, erratum, expression of concern, or retraction

Prefer the version of record for citation. Use an open manuscript for reading only when content equivalence is clear, and record the version used.
