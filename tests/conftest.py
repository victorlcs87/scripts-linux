import pytest

from reforja import installers, platform


@pytest.fixture(autouse=True)
def _reset_ecosystem_caches():
    """Zera os memos por-processo (flathub/rpmfusion) entre testes."""
    installers._reset_ecosystem_cache()
    platform._reset_ecosystem_cache()
    yield
    installers._reset_ecosystem_cache()
    platform._reset_ecosystem_cache()
