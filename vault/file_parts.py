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
  feed does not count), and **the length** is the number of UTF-16 code units of the whole text. Both are counted by the shell on the
  whole file in one small read of its own (a few digits that no output cap can cut), before the file is read in parts, so a read cut
  short shows as fewer. The server checks both, and the checksum, against what it assembled.
* **Verification** happens when every part is present and before anything is previewed: line count, then length, then checksum, then
  that the parts join into valid text. A mismatch is refused, the parts are deleted, and nothing is staged.
* **The size** is the one import-size limit (``IMPORT_MAX_BYTES``, ``vault.config``) over the stored parts, checked as each part
  arrives; the hosting platform's request limit bounds each part, not the total.
* **Part size.** The assistant's shell returns at most ``max_output_tokens`` (10,000 by default) per command, and a CSV is about 2.5
  characters per token in the worst case; with a 35% margin that is 16,384 bytes of the file (cut at a character boundary, so a part
  never holds half a character), and the script halves it once, to 8,192, when a read comes back truncated or does not match the
  file's own length and line count. Nothing is sent until every read of a size has succeeded, so a pass is at most 8 MiB / 8,192 =
  1,024 calls, which is what ``IMPORT_PART_LIMIT`` (1,200 a minute) is derived from.
* **The shell** the script is written for is PowerShell (Windows PowerShell 5.1 or PowerShell 7): the coding apps on Windows. On macOS
  or Linux the Codex shell is not PowerShell and the script does not apply; ``steps`` says so.
* **The path** is never spliced into a command: the script writes it as hex digits of its UTF-16 code units, which the command turns
  back into the path inside PowerShell, so no character of a file name (a curly quote, a backtick, ``$()``, ``;``) can end a string
  or run anything (review of PR #483: a name with U+2019 in it ran an injected command when only the ASCII quote was doubled).
"""

from __future__ import annotations

import hashlib
import json
import zlib
from collections.abc import Iterable

from . import config

MARKER = "intake-"  # the start of the ticket_hash of a file a chat app handed over (vault.uploads.INTAKE_MARKER)
MIN_PARTS_CAP = 4096
MAX_LISTED = 50  # missing parts named in an answer
FILE_NAME = "chat-upload.csv"
UNITS = "utf-16-le"
PART_BYTES = 16384  # first part size the script reads (bytes of the file)
MIN_PART_BYTES = 8192  # the smallest: 8 MiB / 8,192 = 1,024 calls in a pass, inside IMPORT_PART_LIMIT
MAX_PASS_PARTS = 1024  # the script sends no more parts than this in a pass (the default limit / MIN_PART_BYTES); raise it with IMPORT_PART_LIMIT
TOKEN_BUDGET = 10000  # exec_command's default max_output_tokens
REJECT_TOKENS = 9000  # a read that reports more than this was probably truncated
PARALLEL = 8  # reads, and sends, at a time


def max_parts() -> int:
    """Parts a file may have: enough for a file of the import-size limit in parts of the smallest size the script uses (1,024 for
    8 MiB), and never fewer than 4,096."""
    return max(MIN_PARTS_CAP, -(-config.import_max_bytes() // MIN_PART_BYTES))


def set_key(user_id: int, total_sum: int, parts: int, lines: int, chars: int, bucket_id: int | None) -> str:
    """The ticket_hash of the staged upload a file's parts belong to: the person and the file's own identity (checksum, number of
    parts, line count, length) and bucket, so parts sent in any order, or again, reach the same row, and nobody else's reach it. 7 + 56 hex
    characters, never a SHA-256 digest (63 characters), like every file a chat app handed over."""
    raw = f"parts|{user_id}|{total_sum}|{parts}|{lines}|{chars}|{bucket_id}".encode()
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
    """The parts do not make the file they say they do. ``reason`` is ``lines``, ``length``, ``checksum`` or ``text``."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def assemble(chunks: Iterable[bytes], lines: int, chars: int, total_sum: int) -> bytes:
    """The file's bytes (UTF-8) from its stored parts in order, after the checks: line feeds counted over all parts equal ``lines``,
    the number of UTF-16 code units equals ``chars``, the CRC-32 over their UTF-16 code units equals ``total_sum``, and the parts join into valid text (a pair split between two parts
    is joined; half a pair left over is not valid). Raises ``Mismatch``. Part count and contiguity are the caller's (it has the
    indexes)."""
    crc, newlines, units, texts = 0, 0, 0, []
    for data in chunks:
        text = data.decode("utf-8", "surrogatepass")
        crc, newlines, units = crc_of(text, crc), newlines + line_count(text), units + len(text.encode(UNITS, "surrogatepass")) // 2
        texts.append(text)
    if newlines != lines:
        raise Mismatch("lines")
    if units != chars:
        raise Mismatch("length")
    if crc != total_sum:
        raise Mismatch("checksum")
    try:
        whole = "".join(texts).encode(UNITS, "surrogatepass").decode(UNITS)
    except UnicodeDecodeError as exc:
        raise Mismatch("text") from exc
    return whole.encode("utf-8")


# -- what the assistant is told ------------------------------------------------------------------------------------------------------

# The script a coding assistant runs. <PATH> is the only thing it fills in, and it goes into the script as a JavaScript string and
# reaches the shell only as hex digits, never as text of a command. Every PowerShell command is one line with single quotes only
# and no backslash (a command line may mangle double quotes), and prints only ASCII (the part is JSON-escaped, so no console encoding
# can change it). One tiny command first prints the file's length from FileInfo, and nothing is read when that is over the size limit; then one
# small read prints four numbers (the text's length in UTF-16 code units, its line feeds, the bytes after any
# byte-order mark, and the mark's size) that no output cap can cut; each part is then read by byte offsets, cut forward to a character
# boundary, so no part holds half a character and no read opens the whole file. A read is rejected when it reports more than
# REJECT_TOKENS tokens, does not parse or is not valid UTF-8; the file is then read again in halves, once. Nothing is sent until the
# parts joined have exactly the length and line count of that first read. A send that fails is retried only when throttled, and the
# script stops, naming the parts that failed, when it is not.
RECIPE = r"""const P="<PATH>";
const sh=c=>tools.exec_command({cmd:c,login:false}),par=n=>Array.from({length:n},(_,j)=>j);
const T=[];for(let n=0,c;n<256;n++){c=n;for(let k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;T[n]=c>>>0}
const crc=a=>{let h=-1;for(const s of a)for(let i=0;i<s.length;i++){const u=s.charCodeAt(i);h=T[(h^u)&255]^(h>>>8);h=T[(h^(u>>>8))&255]^(h>>>8)}return(h^-1)>>>0};
const last=r=>r.output.trim().split(/\r?\n/).pop();
const H=Array.from({length:P.length},(_,i)=>P.charCodeAt(i).toString(16).padStart(4,"0")).join("");
const rd="$h='"+H+"';$p=-join(0..($h.Length/4-1)|%{[char][Convert]::ToInt32($h.Substring($_*4,4),16)});";
const r1=await sh(rd+"[Console]::Out.Write([IO.FileInfo]::new($p).Length)"),sz=!(r1.original_token_count>=100)&&/^\d+$/.test(last(r1))?Number(last(r1)):NaN;
const big={status:"too_large",changed:false,say:@SAYBIG@},ok=["part_received","uploaded","already_staged"];
let res;
if(sz>@MAXB@+3)res=big;
const r0=res||isNaN(sz)?null:await sh(rd+"$b=[IO.File]::ReadAllBytes($p);$o=0;if($b.Length -ge 3 -and $b[0] -eq 239 -and $b[1] -eq 187 -and $b[2] -eq 191){$o=3};$t=[Text.UTF8Encoding]::new($false,$true).GetString($b,$o,$b.Length-$o);[Console]::Out.Write($t.Length.ToString()+','+($t.Split([char]10).Length-1)+','+($b.Length-$o)+','+$o)");
const m=r0&&!(r0.original_token_count>=100)&&/^\d+,\d+,\d+,\d+$/.test(last(r0))?last(r0).split(",").map(Number):null;
if(m){const[L,K,Z,O]=m;
if(Z>@MAXB@)res=big;
for(let N=@PART@;N>=@MIN@&&!res;N=N>>1){
 const n=Math.max(1,Math.ceil(Z/N)),rs=[];
 if(n>@MAXP@){res=big;break}
 const get=async k=>{const r=await sh(rd+"$f=[IO.File]::OpenRead($p);function b($k){if($k -le 0){return "+O+"};$q=[Math]::Min($f.Length,"+O+"+$k*"+N+");if($q -ge $f.Length){return $f.Length};$f.Position=$q;for($i=0;$i -lt 3;$i++){$c=$f.ReadByte();if($c -lt 128 -or $c -gt 191){break};$q++};return $q};$s=(b "+k+");$e=(b "+(k+1)+");$f.Position=$s;$m=[IO.BinaryReader]::new($f).ReadBytes($e-$s);$f.Close();$t=[Text.UTF8Encoding]::new($false,$true).GetString($m);[Console]::Out.Write([regex]::Replace(($t|ConvertTo-Json -Compress),'[^ -~]',{param($x)'\\u{0:x4}' -f [int][char]$x.Value}))");try{return r.original_token_count>@REJECT@?null:JSON.parse(last(r))}catch{return null}};
 for(let i=0;i<n;i+=@PARALLEL@)rs.push(...await Promise.all(par(Math.min(@PARALLEL@,n-i)).map(j=>get(i+j))));
 const s=rs.join("");
 if(rs.includes(null)||s.length!==L||s.split("\n").length-1!==K)continue;
 const sum=crc(rs),a=[];let halt=false;
 const send=async i=>{if(halt)return{status:"skipped"};for(let t=0;;t++){const r=await tools.mcp__the_vault__import_collection_file({text:rs[i],part:i+1,parts:n,lines:K,chars:L,sum@BUCKET@}),c=r.structuredContent||{};if(!r.isError&&c.status){if(!ok.includes(c.status))halt=true;return c}if(t<4&&(c.status===429||/Too many/.test(JSON.stringify(r.content)))){await sh("Start-Sleep -Seconds "+Math.min(60,c.retry_after_seconds||5));continue}halt=true;return{status:"failed",part:i+1}}};
 for(let i=0;i<n&&!halt;i+=@PARALLEL@)a.push(...await Promise.all(par(Math.min(@PARALLEL@,n-i)).map(j=>send(i+j))));
 const bad=a.find(x=>x.status!=="skipped"&&x.status!=="failed"&&!ok.includes(x.status));
 if(a.some(x=>x.status==="failed"))res={status:"stopped",failed_parts:a.filter(x=>x.status==="failed").map(x=>x.part)};
 else if(bad)res=bad;
 else res=a.find(x=>x.status==="uploaded"||x.status==="already_staged")||a.filter(x=>x.status!=="skipped").pop();
}}
text(JSON.stringify(res||{status:"not_read"}));"""


def recipe(bucket_id: int | None = None) -> str:
    """The script with its numbers filled in, and the bucket the call was made for, if any (every part goes into it)."""
    return (RECIPE.replace("@BUCKET@", "" if bucket_id is None else f",bucket_id:{int(bucket_id)}").replace("@PART@", str(PART_BYTES))
            .replace("@MIN@", str(MIN_PART_BYTES)).replace("@REJECT@", str(REJECT_TOKENS)).replace("@PARALLEL@", str(PARALLEL))
            .replace("@MAXB@", str(config.import_max_bytes())).replace("@MAXP@", str(MAX_PASS_PARTS)).replace("@SAYBIG@", json.dumps(
                f"The file is bigger than the {config.size_text(config.import_max_bytes())} the Vault takes, so it was not read "
                "and the collection was not changed.")))


def steps(bucket_id: int | None = None) -> str:
    """What ``next`` says when the call carries neither ``file`` nor ``text``: for the model, never for the person."""
    return (
        "If the attached file's local path is in your context (a line 'Files mentioned by the user: <path>') and your shell is "
        "PowerShell (Windows; on macOS or Linux these commands do not apply, so do not run them), and you can run JavaScript with "
        "tools.exec_command, do not show the person `say`: read the file yourself and send it to this tool in parts, in one script, so "
        "its text never passes through your context. Run this script once for one file (several files, one after the other) with <PATH> set to the path as the inside of a JavaScript "
        "string in double quotes (write / in place of each backslash, and put a backslash before any double quote in it), and nothing "
        "else changed: " + recipe(bucket_id) + "\nIt first reads the file's length and line feeds, then the file in parts of "
        + str(PART_BYTES) + " bytes (halved once, to " + str(MIN_PART_BYTES) + ", when a read comes back truncated or does not match "
        "that length and line count), sends nothing until every part has been read, calls this tool with each part (text, part, parts, "
        "lines, chars, sum), and prints the last answer. It stops at the first answer that is not progress (too large, unreadable, a part "
        "the Vault refuses) and prints it: give the person the one sentence in its `say` and stop, with no link. If it prints not_read "
        "the file could not be read here: give the person the sentence in the `say` you started from, and stop, with no link. Sending a "
        "part again is harmless, so run it again if it was cut short; if it prints the parts it could not send, run it again. Each "
        "answer to a part names the parts still missing. When it prints already_staged the file is complete: call get_staged_upload "
        "with its id for the preview. Then show the person the preview "
        "(what would change, and the few things that deserve a look), ask for one yes, and on their yes call confirm_staged_upload as "
        "that answer's `next` says. Never send the person to a link when a file was attached. If there is no readable path, or your "
        "shell is not PowerShell, give the person `say`.")
