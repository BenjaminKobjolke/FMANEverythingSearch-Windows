"""
Smallest check that fails if the feedback/anti-hang logic breaks.

Run:  python check_everything.py     (exit 0 = ok, 1 = broken)

Deliberately not a pytest suite: this plugin has no test deps, and the one thing
that genuinely needs pinning is that a hung Everything IPC call is abandoned
rather than joined.
"""
import os
import sys
import time
import types


def _stub_fman():
    """Let the plugin import outside fman."""
    fman = types.ModuleType('fman')
    fman.DirectoryPaneCommand = object
    fman.DirectoryPaneListener = object
    fman.show_alert = lambda *a, **kw: None
    fman.show_prompt = lambda *a, **kw: ('', False)
    fman.show_status_message = lambda *a, **kw: None
    fman.clear_status_message = lambda *a, **kw: None

    fs = types.ModuleType('fman.fs')
    fs.FileSystem = object
    fs.Column = object
    fs.cached = lambda fn: fn

    url = types.ModuleType('fman.url')
    url.as_url = lambda p, **kw: p
    url.splitscheme = lambda u: (u.split('://')[0] + '://', u.split('://')[1])
    url.basename = lambda u: u.rsplit('/', 1)[-1]
    url.dirname = lambda u: u.rsplit('/', 1)[0]

    fman.fs = fs
    fman.url = url
    sys.modules.update({'fman': fman, 'fman.fs': fs, 'fman.url': url})


def main():
    _stub_fman()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import everything as e

    # 1. A call that never returns must be abandoned, not joined. If this
    #    regresses to a plain join(), the script hangs for 30s instead of
    #    finishing, and the elapsed assert below fails.
    start = time.time()
    try:
        e._call_with_timeout(lambda: time.sleep(30), 0.2)
    except e.Timeout:
        pass
    else:
        raise AssertionError('_call_with_timeout did not raise Timeout')
    elapsed = time.time() - start
    assert elapsed < 1.0, f'timeout took {elapsed:.1f}s, expected well under 1s'

    # 2. Normal return and exception propagation still work.
    assert e._call_with_timeout(lambda: 42, 5) == 42

    def boom():
        raise ValueError('kaboom')
    try:
        e._call_with_timeout(boom, 5)
    except ValueError as exc:
        assert str(exc) == 'kaboom'
    else:
        raise AssertionError('exception did not propagate out of _call_with_timeout')

    # 3. The failure modes must stay distinguishable. Collapsing any two of these
    #    into one string is exactly the bug this change exists to fix.
    headlines = {e.NOT_RUNNING_MSG, e.HUNG_MSG, e.INDEXING_MSG,
                 'Everything DLL not found.'}
    assert len(headlines) == 4, f'failure messages collapsed: {headlines}'

    # 4. The headline is always one line; the remedy keeps its breaks for the alert
    #    and is flattened only for the pane cell.
    err = e.SearchUnavailable('head\nline', 'multi\nline')
    assert err.headline == 'head line', err.headline
    assert err.remedy == 'multi\nline', repr(err.remedy)
    assert err.cell_remedy == 'multi line', err.cell_remedy

    # 4b. Angle brackets must not survive into an alert: QMessageBox auto-detects
    #     rich text and would eat "<urlopen error ...>" as an unknown tag.
    wrapped = e.SearchUnavailable('head', 'x <urlopen error [WinError 10061]> y')
    assert '<' not in wrapped.remedy and '>' not in wrapped.remedy, wrapped.remedy
    assert 'urlopen error' in wrapped.remedy

    # 4b-ii. QMessageBox sizes a plain-text dialog to its longest line and word-wraps
    #        anything longer into a tall, narrow column. Keep every line short, and
    #        keep the pane's version collapsed to one line.
    def _widest(text):
        return max(len(line) for line in text.split('\n'))

    multiline = e.SearchUnavailable(e.HUNG_MSG, e.HUNG_REMEDY)
    assert '\n' in multiline.alert_text, 'remedy lost its line breaks'
    assert '\n' not in multiline.cell_remedy, 'pane cell must be a single line'
    assert 'Everything service' in multiline.cell_remedy

    # Every message that can reach a dialog, including the combined DLL+API one
    # built at runtime - that is the one that was too wide.
    for label, headline, remedy in [
        ('hung', e.HUNG_MSG, e.HUNG_REMEDY),
        ('not running', e.NOT_RUNNING_MSG, e.NOT_RUNNING_REMEDY),
        ('indexing', e.INDEXING_MSG, e.INDEXING_REMEDY),
        ('dll+api', e.HUNG_MSG,
         e.HUNG_REMEDY + '\n\nThe API server is not reachable either.\n'
                         'See fman_everything.log in your temp folder.'),
    ]:
        width = _widest(e.SearchUnavailable(headline, remedy).alert_text)
        assert width <= 60, f'{label!r} alert line is {width} chars - dialog will wrap'

    # 4c. A failed search must alert and must NOT navigate; a successful one must
    #     navigate. This is the whole point of searching before set_path.
    alerts, paths = [], []

    class _Pane:
        def set_path(self, p, **kw):
            paths.append(p)

    class _Cmd:
        pane = _Pane()

    original_alert = e.show_alert
    original_prompt = e.show_prompt
    original_search = e.search_everything
    try:
        e.show_alert = lambda text, *a, **kw: alerts.append(text)
        e.show_prompt = lambda *a, **kw: ('gutschein', True)
        e.EverythingFS._results_cache.clear()

        def _fail(q):
            raise e.SearchUnavailable(e.HUNG_MSG, e.HUNG_REMEDY)
        e.search_everything = _fail
        e._set_everything_path(_Cmd())
        assert paths == [], 'navigated to a pane despite a failed search'
        assert len(alerts) == 1 and e.HUNG_MSG in alerts[0], alerts

        alerts.clear()
        e.search_everything = lambda q: (0, [])
        e._set_everything_path(_Cmd())
        assert paths == [], 'navigated despite zero results'
        assert len(alerts) == 1 and 'No files matching' in alerts[0], alerts

        alerts.clear()
        e.search_everything = lambda q: (1, [{'filename': 'a', 'path': 'p',
                                              'size': 0, 'date_modified': ''}])
        e._set_everything_path(_Cmd())
        assert alerts == [], f'alerted on a successful search: {alerts}'
        assert paths == ['everything:///gutschein'], paths
    finally:
        e.show_alert = original_alert
        e.show_prompt = original_prompt
        e.search_everything = original_search
        e.EverythingFS._results_cache.clear()
        e.EverythingFS._last_error = None

    # 5. The placeholder row must be recognised at the url fman actually builds.
    #    fman joins the yielded name onto the current directory, so a bare
    #    `path == 'placeholder'` never matches and the raw word leaks into the pane.
    assert e._is_placeholder('placeholder')
    assert e._is_placeholder('/gutschein%20psd/placeholder')
    assert e._is_placeholder('/placeholder')
    assert not e._is_placeholder('/term/0')
    assert not e._is_placeholder('/my placeholder file/3')

    name_col, path_col = e.Name(), e.Path()
    url = 'everything:///term/placeholder'
    e.EverythingFS._last_error = ('HEADLINE', 'REMEDY')
    assert name_col.get_str(url) == 'HEADLINE', name_col.get_str(url)
    assert path_col.get_str(url) == 'REMEDY', path_col.get_str(url)
    e.EverythingFS._last_error = None
    assert 'Everything' in name_col.get_str(url)

    # 6. A search that matched nothing must NOT look like a failure, must not be
    #    cached, and must not raise. Before this change both rendered as a blank
    #    pane and the empty result was cached for the life of the process.
    fs = e.EverythingFS()
    original = e.search_everything
    try:
        e.search_everything = lambda q: (0, [])
        e.EverythingFS._results_cache.clear()
        e.EverythingFS._last_error = None
        fs.update_search('nomatch')
        assert e.EverythingFS._last_error is not None, 'no feedback for zero results'
        assert 'No files matching' in e.EverythingFS._last_error[0], \
            e.EverythingFS._last_error
        assert 'nomatch' not in e.EverythingFS._results_cache, \
            'empty result set was cached'

        # ...and a search that did match clears the message again.
        e.search_everything = lambda q: (1, [{'filename': 'a', 'path': 'p',
                                              'size': 0, 'date_modified': ''}])
        fs.update_search('hit')
        assert e.EverythingFS._last_error is None, 'stale message survived a hit'
        assert 'hit' in e.EverythingFS._results_cache
    finally:
        e.search_everything = original
        e.EverythingFS._results_cache.clear()
        e.EverythingFS._last_error = None

    print('OK - all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
