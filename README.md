# Software-Utilities
Software utilities for data manipulation of files that are input or exported out of Kenexis software products.  These utilities are primarily skill files to be consumed by LLM tools to create and manipulate data files employed by Kenexis software products.  These tools will allow third party application to manipulate and access data and allow AI tools to consume and modify the contents of these files.

## Development

Run the tests from the repository root:

```
python -m unittest discover tests
```

`skills/kenexis-open-pha-writer/pha_templates.json` is the application's own empty study for each Open-PHA mode pair. When Open-PHA's `Ds_Rev` changes, replace it with a fresh dump from the application (`OpenPha/Create/openpha-default-documents.json` in the KISS repository) so that the writer stays current.

`skills/kenexis-open-audit-writer/aud_template.json` is the same for Open-Audit: a dump of the application's `new kxsStudyData()` with its link lists pruned, as saved. Scoring in `write_aud.py` is a port of the application's `KxsCalc.js`, so a change to that calculation has to be made in both places.
