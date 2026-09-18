from testlib import repo_path


DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")


def test_jupyterlab_slurm_source_and_builder_are_reproducibly_pinned():
    assert (
        'ARG JUPYTERLAB_SLURM_REF="c34354f0aaa1b12f6243224bed631cf07c858409"'
        in DOCKERFILE
    )
    assert "git -C /tmp/jupyterlab-slurm checkout --detach" in DOCKERFILE
    assert 'ARG JUPYTER_BUILDER_VERSION=1.2.3' in DOCKERFILE


def test_jupyterlab_slurm_obsolete_builder_rewrite_is_anchored():
    assert "@jupyterlab/builder" in DOCKERFILE
    assert '.devDependencies["@jupyter/builder"] = $version' in DOCKERFILE
    assert '"jupyterlab-slurm @ file:///tmp/jupyterlab-slurm"' in DOCKERFILE
