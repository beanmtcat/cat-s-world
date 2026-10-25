from argparse import Namespace
from io import BytesIO

import pytest
from PIL import Image

from app.cli import backfill_thumbnails as command


@pytest.mark.asyncio
@pytest.mark.parametrize("include_media", [False, True])
async def test_candidates_construct_queries_from_real_model_fields(include_media):
    """Do not stub candidates: building the Page.cover_url query used to crash."""
    queried = []
    responses = iter(
        [
            ["https://images.example/post.png"],
            ["https://images.example/gallery.png"],
            ["文字动态", "![配图](https://images.example/feed.png)"],
            ["https://images.example/attachment.png"],
        ]
    )

    class Session:
        async def stream_scalars(self, statement):
            queried.append(str(statement.compile()))

            async def rows():
                for value in next(responses):
                    yield value

            return rows()

    urls = [url async for url in command.candidates(Session(), include_media)]
    assert urls == [
        "https://images.example/post.png",
        "https://images.example/gallery.png",
        "https://images.example/feed.png",
        *(["https://images.example/attachment.png"] if include_media else []),
    ]
    assert not any("mmcat_pages" in query for query in queried)
    assert len(queried) == (4 if include_media else 3)


@pytest.mark.asyncio
async def test_backfill_preserves_source_links_and_skips_completed_images(monkeypatch):
    mappings, uploads = {}, []
    original_url = "https://images.example/legacy.png"
    source = BytesIO()
    Image.new("RGB", (2000, 1000), "blue").save(source, format="PNG")

    class Session:
        pending = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, model, url):
            return mappings.get(url)

        def add(self, item):
            self.pending = item

        async def commit(self):
            mappings[self.pending.source_url] = self.pending

    async def candidates(*args):
        yield original_url
        yield original_url

    def put(key, content, mime_type):
        uploads.append((key, content.read(), mime_type))

    monkeypatch.setattr(command, "SessionLocal", Session)
    monkeypatch.setattr(command, "candidates", candidates)
    monkeypatch.setattr(command, "read_source", lambda *args: source.getvalue())
    monkeypatch.setattr(command, "_put_s3_object", put)
    monkeypatch.setattr(command, "_upload_object_key", lambda filename: filename)
    monkeypatch.setattr(command, "_public_media_url", lambda key: "https://images.example/" + key)
    args = Namespace(apply=False, limit=100, include_media=False, source_base_url=[])
    await command.run(args)
    assert not mappings and not uploads
    args.apply = True
    await command.run(args)
    await command.run(args)
    assert len(uploads) == len(mappings) == 1
    assert mappings[original_url].thumbnail_url.endswith("thumb-640.webp")
    assert Image.open(BytesIO(uploads[0][1])).size == (640, 320)


def test_legacy_source_allowlist_checks_origin_and_path_boundary():
    assert command._under_base("https://images.example/halo/a.png", "https://images.example/halo")
    assert not command._under_base(
        "https://images.example/halo-evil/a.png", "https://images.example/halo"
    )
    assert not command._under_base(
        "https://other.example/halo/a.png", "https://images.example/halo"
    )


@pytest.mark.asyncio
async def test_failures_do_not_starve_success_limit_and_are_summarized(monkeypatch, capsys):
    mappings, reads = {}, []
    source = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(source, format="PNG")
    bad = [f"https://images.example/bad-{index}.png" for index in range(3)]
    good = [f"https://images.example/good-{index}.png" for index in range(3)]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, model, url):
            return mappings.get(url)

        def add(self, item):
            self.pending = item

        async def commit(self):
            mappings[self.pending.source_url] = self.pending

    async def candidates(*args):
        for url in [*bad, bad[0], *good]:
            yield url

    def read_source(url, bases):
        reads.append(url)
        if url in bad:
            raise ValueError("corrupt image")
        return source.getvalue()

    monkeypatch.setattr(command, "SessionLocal", Session)
    monkeypatch.setattr(command, "candidates", candidates)
    monkeypatch.setattr(command, "read_source", read_source)
    monkeypatch.setattr(command, "_put_s3_object", lambda *args: None)
    monkeypatch.setattr(command, "_upload_object_key", lambda filename: filename)
    monkeypatch.setattr(command, "_public_media_url", lambda key: "https://images.example/" + key)
    args = Namespace(apply=True, limit=2, include_media=False, source_base_url=[])
    with pytest.raises(SystemExit) as result:
        await command.run(args)
    assert result.value.code == 1
    assert reads == [*bad, *good[:2]]
    assert set(mappings) == set(good[:2])
    summary = capsys.readouterr().out.split("失败清单：")[1]
    assert all(url in summary for url in bad)
    # A subsequent run must get past the same bad prefix to the remaining good URL.
    with pytest.raises(SystemExit):
        await command.run(args)
    assert set(mappings) == set(good)
