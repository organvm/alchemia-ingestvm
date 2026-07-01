"""Tests for broad CLI command behavior."""

import json
import sys
from argparse import Namespace

import pytest

from alchemia import cli


def test_cmd_intake_writes_enriched_deduped_inventory(monkeypatch, tmp_path):
    from alchemia.intake import crawler, dedup, manifest_loader

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("path,title\n")
    output = tmp_path / "inventory.json"
    calls = []

    def fake_crawl(source_dirs):
        calls.append(("crawl", [str(path) for path in source_dirs]))
        return [{"path": str(source_dir / "note.md"), "filename": "note.md"}]

    def fake_enrich_from_manifest(inventory, manifest_path):
        calls.append(("manifest", str(manifest_path)))
        inventory[0]["manifest"] = {"manifest_category": "Technical Specifications"}
        return inventory

    def fake_enrich_from_sidecars(inventory):
        calls.append(("sidecars", None))
        inventory[0]["sidecar"] = True
        return inventory

    def fake_mark_duplicates(inventory):
        calls.append(("dedup", None))
        inventory[0]["is_duplicate"] = False
        return inventory

    monkeypatch.setattr(crawler, "crawl", fake_crawl)
    monkeypatch.setattr(manifest_loader, "enrich_from_manifest", fake_enrich_from_manifest)
    monkeypatch.setattr(manifest_loader, "enrich_from_sidecars", fake_enrich_from_sidecars)
    monkeypatch.setattr(dedup, "mark_duplicates", fake_mark_duplicates)

    cli.cmd_intake(
        Namespace(
            source_dir=[str(source_dir)],
            manifest=str(manifest),
            output=str(output),
        ),
    )

    data = json.loads(output.read_text())
    assert data["stage"] == "intake"
    assert data["source_dirs"] == [str(source_dir)]
    assert data["total_files"] == 1
    assert data["entries"][0]["manifest"]["manifest_category"] == "Technical Specifications"
    assert data["entries"][0]["sidecar"] is True
    assert data["entries"][0]["is_duplicate"] is False
    assert [call[0] for call in calls] == ["crawl", "manifest", "sidecars", "dedup"]


def test_cmd_absorb_classifies_inventory_and_writes_mapping(monkeypatch, tmp_path):
    from alchemia.absorb import classifier, registry_loader

    inventory = tmp_path / "intake-inventory.json"
    inventory.write_text(json.dumps({"entries": [{"filename": "note.md"}]}))
    output = tmp_path / "absorb-mapping.json"
    registry = {"repos": ["repo-a", "repo-b"], "archived": {"old-repo"}}

    def fake_classify_all(entries, loaded_registry):
        assert loaded_registry is registry
        entries[0]["classification"] = {
            "status": "CLASSIFIED",
            "target_repo": "repo-a",
        }
        return entries

    monkeypatch.setattr(registry_loader, "load_registry", lambda: registry)
    monkeypatch.setattr(classifier, "classify_all", fake_classify_all)

    cli.cmd_absorb(Namespace(inventory=str(inventory), output=str(output)))

    data = json.loads(output.read_text())
    assert data["stage"] == "absorb"
    assert data["source_inventory"] == str(inventory)
    assert data["total_entries"] == 1
    assert data["entries"][0]["classification"]["target_repo"] == "repo-a"


def test_cmd_alchemize_dry_run_filters_by_organ(monkeypatch, tmp_path, capsys):
    from alchemia.absorb import registry_loader
    from alchemia.alchemize import provenance

    mapping = tmp_path / "absorb-mapping.json"
    entries = [
        {"filename": "a.md", "classification": {"target_organ": "ORGAN-I"}},
        {"filename": "b.md", "classification": {"target_organ": "ORGAN-II"}},
    ]
    mapping.write_text(json.dumps({"entries": entries}))
    plan = {
        "organ/repo-a": {
            "deploy": [
                {
                    "_deploy_path": "docs/a.md",
                    "classification": {"target_organ": "ORGAN-I"},
                },
            ],
            "convert": [],
            "reference": [{"filename": "ref.md"}],
            "skip": [],
        },
        "organ/repo-b": {
            "deploy": [
                {
                    "_deploy_path": "docs/b.md",
                    "classification": {"target_organ": "ORGAN-II"},
                },
            ],
            "convert": [],
            "reference": [],
            "skip": [],
        },
    }

    monkeypatch.setattr(registry_loader, "load_registry", lambda: {"repos": [], "archived": []})
    monkeypatch.setattr(provenance, "get_deployment_plan", lambda loaded: plan)

    cli.cmd_alchemize(
        Namespace(
            mapping=str(mapping),
            organ="I",
            repo=None,
            dry_run=True,
            force=False,
            batch_size=20,
        ),
    )

    out = capsys.readouterr().out
    assert "Filtered to organ I: 1 repos" in out
    assert "organ/repo-a: 1 files + 1 references" in out
    assert "docs/a.md" in out
    assert "organ/repo-b:" not in out


def test_cmd_alchemize_deploys_manifest_and_writes_provenance(monkeypatch, tmp_path):
    from alchemia.absorb import registry_loader
    from alchemia.alchemize import batch_deployer, provenance

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    mapping = tmp_path / "absorb-mapping.json"
    entries = [{"filename": "a.md", "classification": {"target_repo": "repo-a"}}]
    mapping.write_text(json.dumps({"entries": entries}))
    registry = {"repos": ["repo-a"], "archived": []}
    manifest = {
        "org/repo-a": {
            "org": "org",
            "repo": "repo-a",
            "files": [{"source": "a.md"}],
        },
    }
    deploy_calls = []

    def fake_deploy_repo_batch(org, repo, files, force, batch_size):
        deploy_calls.append(
            {
                "org": org,
                "repo": repo,
                "files": files,
                "force": force,
                "batch_size": batch_size,
            },
        )
        return {"deployed": 1, "skipped": 0, "failed": 0, "errors": []}

    monkeypatch.setattr(registry_loader, "load_registry", lambda: registry)
    monkeypatch.setattr(provenance, "get_deployment_plan", lambda loaded: {})
    monkeypatch.setattr(
        provenance,
        "generate_provenance_registry",
        lambda loaded: {"total_entries": len(loaded)},
    )
    monkeypatch.setattr(batch_deployer, "build_deployment_manifest", lambda loaded, reg: manifest)
    monkeypatch.setattr(batch_deployer, "deploy_repo_batch", fake_deploy_repo_batch)

    cli.cmd_alchemize(
        Namespace(
            mapping=str(mapping),
            organ=None,
            repo=None,
            dry_run=False,
            force=True,
            batch_size=3,
        ),
    )

    assert deploy_calls == [
        {
            "org": "org",
            "repo": "repo-a",
            "files": [{"source": "a.md"}],
            "force": True,
            "batch_size": 3,
        },
    ]
    provenance_file = tmp_path / "data" / "provenance-registry.json"
    assert json.loads(provenance_file.read_text()) == {"total_entries": 1}


def test_cmd_status_reports_present_and_missing_pipeline_files(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "intake-inventory.json").write_text(json.dumps({"total_files": 4}))
    (data_dir / "absorb-mapping.json").write_text(json.dumps({"total_entries": 3}))

    cli.cmd_status(Namespace())

    out = capsys.readouterr().out
    assert "intake-inventory.json: 4 entries" in out
    assert "absorb-mapping.json: 3 entries" in out
    assert "provenance-registry.json: not found" in out


def test_cmd_synthesize_writes_briefs_and_workflow_example(monkeypatch, tmp_path):
    from alchemia import synthesize

    output_dir = tmp_path / "briefs"

    def fake_generate_all_briefs(output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        brief = output_dir / "creative-brief-meta.md"
        brief.write_text("# Meta")
        return [brief]

    monkeypatch.setattr(synthesize, "generate_all_briefs", fake_generate_all_briefs)
    monkeypatch.setattr(
        synthesize,
        "generate_workflow_integration_example",
        lambda: "name: alchemia\n",
    )

    cli.cmd_synthesize(Namespace(output_dir=str(output_dir)))

    assert (output_dir / "creative-brief-meta.md").read_text() == "# Meta"
    assert (output_dir / "workflow-integration-example.yml").read_text() == "name: alchemia\n"


def test_cmd_capture_splits_tags_and_forwards_notes(monkeypatch, capsys):
    from alchemia import aesthetic

    captured = {}

    def fake_add_reference(ref_type, value, tags, notes):
        captured.update(
            {
                "ref_type": ref_type,
                "value": value,
                "tags": tags,
                "notes": notes,
            },
        )
        return {"type": ref_type, "tags": tags, "captured": "2026-01-01T00:00:00"}

    monkeypatch.setattr(aesthetic, "add_reference", fake_add_reference)

    cli.cmd_capture(
        Namespace(
            type="url",
            value="https://example.test",
            tags="art, design ,systems",
            notes="Useful reference",
        ),
    )

    assert captured == {
        "ref_type": "url",
        "value": "https://example.test",
        "tags": ["art", "design", "systems"],
        "notes": "Useful reference",
    }
    assert "Added url reference" in capsys.readouterr().out


def test_cmd_sync_captures_bookmarks_and_skips_missing_google_deps(monkeypatch, capsys):
    from alchemia import aesthetic
    from alchemia.channels import ai_chats, apple_notes, bookmarks, google_docs

    references = []

    def fake_add_reference(ref_type, value, tags, notes):
        references.append(
            {
                "ref_type": ref_type,
                "value": value,
                "tags": tags,
                "notes": notes,
            },
        )
        return {"type": ref_type, "tags": tags, "captured": "now"}

    monkeypatch.setattr(aesthetic, "add_reference", fake_add_reference)
    monkeypatch.setattr(
        bookmarks,
        "sync_bookmarks",
        lambda: [{"url": "https://example.test", "source": "safari", "title": "Example"}],
    )
    monkeypatch.setattr(
        apple_notes,
        "export_alchemia_notes",
        lambda: [{"title": "Note A", "body_length": 42}],
    )
    monkeypatch.setattr(
        google_docs,
        "get_status",
        lambda: {
            "installed": False,
            "authenticated": False,
            "folder_found": False,
            "doc_count": 0,
        },
    )
    monkeypatch.setattr(
        ai_chats,
        "parse_gemini_visits",
        lambda intake_dir: [{"path": "visit.json"}],
    )

    cli.cmd_sync(Namespace(gdocs_folder=None))

    assert references == [
        {
            "ref_type": "url",
            "value": "https://example.test",
            "tags": ["bookmark", "safari"],
            "notes": "From safari: Example",
        },
    ]
    out = capsys.readouterr().out
    assert "Skipped" in out
    assert "Found 1 Gemini visit files" in out


def test_cmd_sync_reports_google_doc_sync_counts(monkeypatch, capsys):
    from alchemia import aesthetic
    from alchemia.channels import ai_chats, apple_notes, bookmarks, google_docs

    sync_calls = []

    monkeypatch.setattr(aesthetic, "add_reference", lambda **kwargs: kwargs)
    monkeypatch.setattr(bookmarks, "sync_bookmarks", lambda: [])
    monkeypatch.setattr(apple_notes, "export_alchemia_notes", lambda: [])
    monkeypatch.setattr(ai_chats, "parse_gemini_visits", lambda intake_dir: [])
    monkeypatch.setattr(
        google_docs,
        "get_status",
        lambda: {
            "installed": True,
            "authenticated": True,
            "folder_found": True,
            "doc_count": 3,
        },
    )

    def fake_sync_google_docs(folder_name):
        sync_calls.append(folder_name)
        return [
            {"name": "Doc A", "status": "synced"},
            {"name": "Doc B", "status": "up_to_date"},
            {"name": "Doc C", "status": "failed"},
        ]

    monkeypatch.setattr(google_docs, "sync_google_docs", fake_sync_google_docs)

    cli.cmd_sync(Namespace(gdocs_folder="Studio"))

    assert sync_calls == ["Studio"]
    out = capsys.readouterr().out
    assert "3 docs in Studio folder: 1 synced, 1 up-to-date, 1 failed" in out
    assert "Doc C [failed]" in out


def test_cmd_gdocs_auth_prints_success(monkeypatch, capsys):
    from alchemia.channels import google_docs

    monkeypatch.setattr(google_docs, "authorize", lambda: True)

    cli.cmd_gdocs_auth(Namespace())

    assert "Authorization successful" in capsys.readouterr().out


def test_cmd_gdocs_auth_exits_when_authorization_fails(monkeypatch):
    from alchemia.channels import google_docs

    monkeypatch.setattr(google_docs, "authorize", lambda: False)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_gdocs_auth(Namespace())

    assert exc.value.code == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            {
                "installed": False,
                "authenticated": False,
                "folder_found": False,
                "doc_count": 0,
            },
            "To set up",
        ),
        (
            {
                "installed": True,
                "authenticated": False,
                "folder_found": False,
                "doc_count": 0,
            },
            "To authenticate",
        ),
        (
            {
                "installed": True,
                "authenticated": True,
                "folder_found": False,
                "doc_count": 0,
            },
            "Create a folder named 'Alchemia'",
        ),
    ],
)
def test_cmd_gdocs_status_prints_next_step(monkeypatch, capsys, status, expected):
    from alchemia.channels import google_docs

    monkeypatch.setattr(google_docs, "get_status", lambda: status)

    cli.cmd_gdocs_status(Namespace())

    assert expected in capsys.readouterr().out


def test_main_dispatches_selected_subcommand(monkeypatch):
    called = {}

    def fake_status(args):
        called["command"] = args.command

    monkeypatch.setattr(cli, "cmd_status", fake_status)
    monkeypatch.setattr(sys, "argv", ["alchemia", "status"])

    cli.main()

    assert called == {"command": "status"}


def test_main_without_command_prints_help_and_exits(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["alchemia"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert "The Alchemical Forge" in capsys.readouterr().out
