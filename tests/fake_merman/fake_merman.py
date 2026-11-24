# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# SPDX-License-Identifier: MIT
"""A fake merman-cli: `render IN -o OUT -f svg -q`. A source containing "fail" exits 1 with a message."""
import sys

args = sys.argv[1:]
src, out = args[1], args[args.index("-o") + 1]
text = open(src, encoding="utf-8").read()
if "fail" in text:
    sys.stderr.write("Parse error on line 1\n")
    sys.exit(1)
with open(out, "w", encoding="utf-8") as f:
    f.write('<svg xmlns="http://www.w3.org/2000/svg"><text>%d</text></svg>' % len(text))
