"""A collection file sent in ordered parts of text (vault-0n9.3).

A coding assistant (the ChatGPT desktop app in code mode, Codex) is given no file object for a file the person drops in the chat,
only its local path. It can read that file in parts with its shell and pass each part to ``import_collection_file`` in the same
script, so the file's text never passes through the model's own context. This module holds the pure parts of that route; the
route itself, with the database, is ``vault.intake``.

* **The checksum** is CRC-32 (the one ``zlib.crc32`` computes) over the **UTF-16 code units** of the whole text, each as two bytes,
  low byte first, in order of the parts. A script computes it in plain JavaScript with no crypto: the same table-driven loop as
  ``RECIPE`` below, over ``charCodeAt``. The server computes it over the parts it stored, in order. Test vectors with non-ASCII
  text, CRLF and a byte-order mark are in ``tests/test_file_parts.py``, and the recipe itself is run with Node there.
* **The line count** is the number of line feed characters in the whole text (a CRLF line counts once, a last line with no line
  feed does not count), counted by the shell on the whole file before it is read in parts, so a read cut short shows as fewer.
* **Verification** happens when every part is present and before anything is previewed: line count, then checksum, then that the
  parts join into valid text. A mismatch is refused, the parts are deleted, and nothing is staged.
* **The size** is the one import-size limit (``IMPORT_MAX_BYTES``, ``vault.config``) over the stored parts, checked as each part
  arrives; the hosting platform's request limit bounds each part, not the total.
* **Part size.** The assistant's shell returns at most ``max_output_tokens`` (10,000 by default) per command, and a CSV is about 2.5
  characters per token in the worst case; with a 35% margin that is 16,384 characters, and the script halves it (down to 1,024)
  when a read comes back truncated or does not match the file's own line count.
"""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import Iterable

MARKER = "intake-"  # the start of the ticket_hash of a file a chat app handed over (vault.uploads.INTAKE_MARKER)
MAX_PARTS = 4096  # a file in parts of 1,024 characters or more, at the largest size the Vault takes, has fewer
MAX_LISTED = 50  # missing parts named in an answer
FILE_NAME = "chat-upload.csv"
UNITS = "utf-16-le"
PART_CHARS = 16384  # first part size the recipe uses
MIN_PART_CHARS = 1024
TOKEN_BUDGET = 10000  # exec_command's default max_output_tokens
REJECT_TOKENS = 9000  # a read that reports more than this was probably truncated
PARALLEL = 8  # reads, and sends, at a time


def set_key(user_id: int, total_sum: int, parts: int, lines: int, bucket_id: int | None) -> str:
    """The ticket_hash of the staged upload a file's parts belong to: the person and the file's own identity (checksum, number of
    parts, line count) and bucket, so parts sent in any order, or again, reach the same row, and nobody else's reach it. 7 + 56 hex
    characters, never a SHA-256 digest (63 characters), like every file a chat app handed over."""
    raw = f"parts|{user_id}|{total_sum}|{parts}|{lines}|{bucket_id}".encode()
    return MARKER + hashlib.sha256(raw).hexdigest()[:56]


def part_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def to_bytes(text: str) -> bytes:
    """A part as stored. ``surrogatepass``: a script that cuts between the two halves of a character outside the Basic Multilingual
    Plane sends half a pair; the halves are joined again when the parts are put together (``assemble``)."""
    return text.encode("utf-8", "surrogatepass")


def crc_of(text: str, running: int = 0) -> int:
    """CRC-32 over the UTF-16 code units of ``text``, continuing from ``running`` (so the parts, in order, give the whole's)."""
    return zlib.crc32(text.encode(UNITS, "surrogatepass"), running)


def checksum(text: str) -> int:
    return crc_of(text)


def line_count(text: str) -> int:
    return text.count("\n")


def ranges(numbers: Iterable[int]) -> str:
    """Part numbers as words: ``part 3``, ``parts 3 and 7``, ``parts 3 to 9, 12 and 15``."""
    nums = sorted(set(numbers))
    groups: list[list[int]] = []
    for n in nums:
        if groups and n == groups[-1][-1] + 1:
            groups[-1].append(n)
        else:
            groups.append([n])
    words = [str(g[0]) if len(g) == 1 else f"{g[0]} to {g[-1]}" if len(g) > 2 else f"{g[0]} and {g[1]}" for g in groups]
    joined = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return f"{'part' if len(nums) == 1 else 'parts'} {joined}"


class Mismatch(Exception):
    """The parts do not make the file they say they do. ``reason`` is ``lines``, ``checksum`` or ``text``."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def assemble(chunks: Iterable[bytes], lines: int, total_sum: int) -> bytes:
    """The file's bytes (UTF-8) from its stored parts in order, after the checks: line feeds counted over all parts equal ``lines``,
    the CRC-32 over their UTF-16 code units equals ``total_sum``, and the parts join into valid text (a pair split between two parts
    is joined; half a pair left over is not valid). Raises ``Mismatch``. Part count and contiguity are the caller's (it has the
    indexes)."""
    crc, newlines, texts = 0, 0, []
    for data in chunks:
        text = data.decode("utf-8", "surrogatepass")
        crc, newlines = crc_of(text, crc), newlines + line_count(text)
        texts.append(text)
    if newlines != lines:
        raise Mismatch("lines")
    if crc != total_sum:
        raise Mismatch("checksum")
    try:
        whole = "".join(texts).encode(UNITS, "surrogatepass").decode(UNITS)
    except UnicodeDecodeError as exc:
        raise Mismatch("text") from exc
    return whole.encode("utf-8")


# -- what the assistant is told ------------------------------------------------------------------------------------------------------

# The script a coding assistant runs. <PATH> is the only thing it fills in. Every PowerShell command is one line with single quotes only
# and no backslash (a command line may mangle double quotes), and prints only ASCII (the text is JSON-escaped, so no console encoding
# can change it). The plan command prints the file's line count and the cut offsets (never inside a surrogate pair); each read prints one
# part as a JSON string. A read is rejected when it reports more than REJECT_TOKENS tokens or does not parse; the whole file is then
# read again in halves. The parts are checked against the shell's own count of the file (length and lines) before anything is sent.
RECIPE = r"""const P=String.raw`<PATH>`;
const q=P.replace(/'/g,"''"),sh=c=>tools.exec_command({cmd:c,login:false});
const T=[];for(let n=0,c;n<256;n++){c=n;for(let k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;T[n]=c>>>0}
const crc=a=>{let h=-1;for(const s of a)for(let i=0;i<s.length;i++){const u=s.charCodeAt(i);h=T[(h^u)&255]^(h>>>8);h=T[(h^(u>>>8))&255]^(h>>>8)}return(h^-1)>>>0};
const last=r=>r.output.trim().split(/\r?\n/).pop();
const rd="$t=[IO.File]::ReadAllText('"+q+"');";
const par=n=>Array.from({length:n},(_,j)=>j);
let res;
for(let N=@PART@;N>=@MIN@&&!res;N=N>>1){
 const p=last(await sh(rd+"$c=@(0);$i="+N+";while($i -lt $t.Length){if([char]::IsLowSurrogate($t[$i])){$i--};$c+=$i;$i+=@@N};$c+=$t.Length;[Console]::Out.Write(($t.Split([char]10).Length-1).ToString()+','+($c -join ','))")).split(',').map(Number);
 const lines=p[0],cut=p.slice(1),n=cut.length-1,rs=[];
 if(p.some(isNaN)||n<1)continue;
 const get=async k=>{const r=await sh(rd+"[Console]::Out.Write([regex]::Replace(($t.Substring("+cut[k]+","+(cut[k+1]-cut[k])+")|ConvertTo-Json -Compress),'[^ -~]',{param($m)'\\u{0:x4}' -f [int][char]$m.Value}))");try{return r.original_token_count>@REJECT@?null:JSON.parse(last(r))}catch{return null}};
 for(let i=0;i<n;i+=@PARALLEL@)rs.push(...await Promise.all(par(Math.min(@PARALLEL@,n-i)).map(j=>get(i+j))));
 const s=rs.join('');
 if(rs.includes(null)||s.length!==cut[n]||s.split('\n').length-1!==lines)continue;
 const sum=crc(rs),ans=[];
 for(let i=0;i<n;i+=@PARALLEL@)ans.push(...await Promise.all(par(Math.min(@PARALLEL@,n-i)).map(j=>tools.mcp__the_vault__import_collection_file({text:rs[i+j],part:i+j+1,parts:n,lines,sum}))));
 const a=ans.map(x=>x.structuredContent||{});
 res=a.find(x=>x.status==='uploaded')||(a.some(x=>x.status==='mismatch')?null:a[a.length-1]);
}
text(JSON.stringify(res||{status:'not_read'}));"""


def recipe() -> str:
    """The script with its numbers filled in. In the PowerShell text ``@@N`` stays the part size of the loop in progress."""
    return (RECIPE.replace("@@N", '"+N+"').replace("@PART@", str(PART_CHARS)).replace("@MIN@", str(MIN_PART_CHARS))
            .replace("@REJECT@", str(REJECT_TOKENS)).replace("@PARALLEL@", str(PARALLEL)))


def steps() -> str:
    """What ``next`` says when the call carries neither ``file`` nor ``text``: for the model, never for the person."""
    return (
        "If the attached file's local path is in your context (a line 'Files mentioned by the user: <path>') and you can run "
        "JavaScript with tools.exec_command, do not show the person `say`: read the file yourself and send it to this tool in "
        "parts, in one script, so its text never passes through your context. Run this script once with <PATH> set to the path, "
        "exactly as given, backslashes and all, and nothing else changed: " + recipe() + "\nIt reads the file in parts of " + str(PART_CHARS)
        + " characters (halved, down to " + str(MIN_PART_CHARS) + ", when a read comes back truncated or does not match the "
        "file's own line count), calls this tool with each part (text, part, parts, lines, sum), and prints the last answer. "
        "Sending a part again is harmless, so run it again if it was cut short. Each answer to a part names the parts still "
        "missing. Then show the person that answer's preview (what would change, and the few things that deserve a look), ask "
        "for one yes, and on their yes call confirm_staged_upload as that answer's `next` says. Never send the person to a link "
        "when a file was attached. If there is no readable path, give the person `say`.")
