"""Resume orchestration helpers for interrupted download sessions.

Responsibilities
----------------
- Detect whether a previously saved ``DownloadState`` represents an
  interrupted (incomplete) session.
- Rebuild a ``DownloadQueue`` from that saved state containing only the
  items that still need to be downloaded (i.e. pending and failed items).

``DownloadState`` itself remains responsible solely for persistence.
The logic here intentionally lives *outside* ``DownloadState`` so that
the two concerns stay separate.
"""

from ibis.downloader import DownloadQueue


def has_interrupted_session(state: dict) -> bool:
    """Return ``True`` if *state* represents an incomplete download session.

    A session is considered interrupted when all of the following hold:

    * *state* is a non-empty dict (a state file was previously written).
    * The ``queue`` list is non-empty (at least one item was planned).
    * Fewer items appear in ``completed`` than in ``queue`` (not all items
      finished successfully).

    Parameters
    ----------
    state : dict
        The plain-dict payload returned by :meth:`DownloadState.load_state`.
        An empty dict (file missing or corrupt) is treated as "no previous
        session".
    """
    if not state:
        return False
    queue = state.get("queue", [])
    if not queue:
        return False
    completed = state.get("completed", [])
    return len(completed) < len(queue)


def build_resume_queue(state: dict) -> DownloadQueue:
    """Return a :class:`DownloadQueue` with only the pending and failed items.

    Items already present in the ``completed`` list of *state* are skipped.
    Failed items (items in ``queue`` that are not in ``completed``) are
    **included** so they are retried in the resumed session.

    Items without a ``download_url`` are silently skipped because the engine
    cannot download them.

    Parameters
    ----------
    state : dict
        The plain-dict payload returned by :meth:`DownloadState.load_state`.
    """
    queue_items = state.get("queue", [])
    completed = state.get("completed", [])

    completed_keys = _build_completed_keys(completed)

    links = []
    for item in queue_items:
        if _is_completed(item, completed_keys):
            continue
        url = item.get("download_url", "")
        if not url:
            continue
        links.append(
            {
                "url": url,
                "invoice_id": item.get("invoice_id"),
                "billing_period": item.get("billing_period"),
                "filename": item.get("filename"),
            }
        )

    return DownloadQueue.from_links(links)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_completed_keys(completed: list) -> set:
    """Return a set of ``(invoice_id, billing_period)`` tuples for *completed*.

    Only items whose ``invoice_id`` is not ``None`` are indexed; items
    without an ``invoice_id`` cannot be uniquely identified and are therefore
    not tracked.
    """
    keys: set = set()
    for item in completed:
        invoice_id = item.get("invoice_id")
        if invoice_id is not None:
            keys.add((invoice_id, item.get("billing_period")))
    return keys


def _is_completed(item: dict, completed_keys: set) -> bool:
    """Return ``True`` if *item* appears in *completed_keys*.

    Items whose ``invoice_id`` is ``None`` are never considered completed so
    they are always included in the resume queue.
    """
    invoice_id = item.get("invoice_id")
    if invoice_id is None:
        return False
    return (invoice_id, item.get("billing_period")) in completed_keys
