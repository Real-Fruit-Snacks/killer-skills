# Vendored files

Shipped inside the skill so killer-repo needs no install for them. Both are MIT; their licenses sit beside them.

| File | From | Version | Used by |
|---|---|---|---|
| `github-markdown.css` | [sindresorhus/github-markdown-css](https://github.com/sindresorhus/github-markdown-css), npm `github-markdown-css` | 5.9.0 | `render.mjs`, to style a README the way github.com does, in light and dark |
| `marked.esm.js` | [markedjs/marked](https://github.com/markedjs/marked), npm `marked`, `lib/marked.esm.js` | 18.1.3 | `render.mjs`, for the approximate render when GitHub's Markdown API can't be reached |

To update one, replace the file with the same file from a newer release and change the version here.
