# Third-party notices

This project is licensed under AGPL-3.0. It incorporates the following third-party work,
whose licence terms are reproduced here as those terms require.

## PENTAGRAM Table Editor

- Source: <https://github.com/xmkg/ko-table-editor>
- Author: xmkg
- Licence: MIT, granted in the project README
- Used in: `bakers/kotools/tbl/reader.py`

`bakers/kotools/tbl/reader.py` contains a Python port of the `.tbl` encryption classes from
that project — the Chaos Expansion Feistel cipher with its key schedule, expansion matrix,
permutation table and substitution boxes, and the standard XOR stream cipher. The surrounding
table parser, column typing and file handling are original to this project.

MIT is compatible with AGPL-3.0: the combined work is distributed under AGPL-3.0, and this
notice preserves the MIT attribution for the incorporated portion.

### Licence grant

The upstream repository states its terms in its README rather than in a `LICENSE` file. The
grant reads:

> It is now open-source under MIT license, you can use it for any purpose without any
> restrictions.

Recorded here because the upstream repository carries no `LICENSE` file and names no copyright
holder, so this README statement is the grant being relied on.

### MIT License

```
Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify, merge,
publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING OUT OF OR IN CONNECTION WITH THE SOFTWARE OR OTHERWISE ARISING FROM OR
IN CONNECTION WITH THE SOFTWARE.
```
