import watan_gold_studio as w


def test_plain_txt_is_split_into_chunks():
    text = "\n".join(f"Ini ayat nombor {i} dalam teks Melayu yang panjang." for i in range(300))
    rows = list(w.read_rows("corpus.txt", text.encode("utf-8")))
    assert len(rows) > 1
    assert all(err is None for _, _, err in rows)
    assert max(len(row["text"]) for _, row, _ in rows) <= w.CHUNK_CHARS
    assert [n for n, _, _ in rows] == list(range(1, len(rows) + 1))


def test_chunks_keep_all_text_and_split_long_lines():
    long_line = " ".join(["perkataan"] * 500)
    chunks = list(w.chunk_plain_text(long_line))
    assert all(len(c) <= w.CHUNK_CHARS for c in chunks)
    assert " ".join(chunks).split() == long_line.split()


def test_crlf_text_is_chunked():
    text = "\r\n".join(f"Ayat {i} yang agak panjang untuk ujian chunk." for i in range(200))
    rows = list(w.read_rows("crlf.txt", text.encode("utf-8")))
    assert len(rows) > 1


def test_chat_transcript_txt_still_parses_as_messages():
    text = "User: Apakah ejaan?\nAssistant: Ejaan.\n\nUser: Apa?\nAssistant: Itu."
    rows = list(w.read_rows("chat.txt", text.encode("utf-8")))
    assert len(rows) == 2
    assert all("messages" in row for _, row, _ in rows)
