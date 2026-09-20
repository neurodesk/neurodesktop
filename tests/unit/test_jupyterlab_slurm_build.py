from testlib import repo_path


DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")


def test_jupyterlab_slurm_source_and_builder_are_reproducibly_pinned():
    assert (
        'ARG JUPYTERLAB_SLURM_REF="8dccb39808f8a1b77712a9a5773a7d2601a56683"'
        in DOCKERFILE
    )
    assert "git -C /tmp/jupyterlab-slurm checkout --detach" in DOCKERFILE
    assert 'ARG JUPYTER_BUILDER_VERSION=1.2.3' in DOCKERFILE


def test_jupyterlab_slurm_uses_upstream_current_builder():
    assert '.devDependencies["@jupyter/builder"]' in DOCKERFILE
    assert '= "^${JUPYTER_BUILDER_VERSION}"' in DOCKERFILE
    assert 'del(.devDependencies["@jupyterlab/builder"])' not in DOCKERFILE
    assert '"jupyterlab-slurm @ file:///tmp/jupyterlab-slurm"' in DOCKERFILE
