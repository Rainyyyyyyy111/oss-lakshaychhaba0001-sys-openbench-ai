"""HTTP coverage for run-item pagination and invalid page numbers."""

import json
import re
import urllib.request

import pytest

from openbench.cli import main
from openbench.serve import Dashboard, create_server, serve_in_thread


@pytest.mark.parametrize("total", [500, 501, 1000, 1001])
def test_run_item_pagination(tmp_path, capsys, total):
    dataset = tmp_path / "large.jsonl"
    dataset.write_text(
        "\n".join(
            json.dumps({
                "id": f"item_{i:04d}", "category": "c",
                "prompt": f"<script>alert({i})</script>",
                "expected_answer": f"<b>answer {i}</b>",
            })
            for i in range(total)
        ) + "\n",
        encoding="utf-8",
    )
    experiments = tmp_path / "experiments"
    assert main([
        "run", "--dataset", str(dataset), "--provider", "mock", "--model", "pagination",
        "--experiments-dir", str(experiments), "--no-cache",
    ]) == 0
    capsys.readouterr()
    run_id = Dashboard(experiments).manager.list_runs()[0].run_id
    server = create_server(experiments, port=0)
    serve_in_thread(server)
    url = f"http://127.0.0.1:{server.server_address[1]}/runs/{run_id}"

    def get_page(query=""):
        with urllib.request.urlopen(url + query, timeout=10) as response:
            assert response.status == 200
            assert "default-src 'none'" in response.headers["Content-Security-Policy"]
            return response.read().decode("utf-8")

    def item_ids(body):
        return re.findall(r"<td>(item_\d{4})</td>", body)

    try:
        last_page = (total + 499) // 500
        pages = [get_page(f"?page={number}") for number in range(1, last_page + 1)]
        for number, body in enumerate(pages, start=1):
            expected = [
                f"item_{i:04d}" for i in range((number - 1) * 500, min(number * 500, total))
            ]
            assert item_ids(body) == expected
            assert "<script>" not in body and "&lt;script&gt;" in body
            assert "<b>answer" not in body and "&lt;b&gt;answer" in body
            assert ("Previous</a>" in body) == (number > 1)
            assert ("Next</a>" in body) == (number < last_page)
            if number > 1:
                assert f'href="/runs/{run_id}?page={number - 1}"' in body
            if number < last_page:
                assert f'href="/runs/{run_id}?page={number + 1}"' in body
        assert item_ids(get_page()) == item_ids(pages[0])
        for invalid in ("", "0", "-1", str(last_page + 1), "1.5", "nope", "%3Cscript%3E", "9" * 5000):
            body = get_page("?page=" + invalid)
            assert item_ids(body) == item_ids(pages[0])
            assert "<script>" not in body
        request = urllib.request.Request(url + f"?page={last_page}", method="HEAD")
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200 and response.read() == b""
            assert int(response.headers["Content-Length"]) == len(pages[-1].encode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
