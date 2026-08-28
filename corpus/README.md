# Corpus (untracked)

`private/` holds proprietary book content the gate's leg 3 runs against — never
committed. Populate locally:

    private/t2-133-ms/    shipped, human-reviewed Malay chapters (*.md)
    private/t2-133w-ms/   shipped workbook chapters (*.md)
    private/en/           English source chapters (*.md)

Every newly shipped book gets appended to rules/corpus-registry.json — the gate
grows stronger with the shelf. A required corpus that is missing HALTS the gate
(fail-closed); CI validates legs 1-2 only via --no-corpus.
