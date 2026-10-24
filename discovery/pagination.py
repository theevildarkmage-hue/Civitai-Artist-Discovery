"""Bound cursors from an external service before committing their checkpoints."""


class PaginationStopped(RuntimeError):
    pass


class PageProgress:
    def __init__(self, cursor=None, *, page_limit=1000, empty_limit=3):
        self.seen = {str(cursor)} if cursor is not None else set()
        self.pages = 0
        self.empty_pages = 0
        self.page_limit = page_limit
        self.empty_limit = empty_limit

    def before_request(self):
        if self.pages >= self.page_limit:
            raise PaginationStopped('Civitai pagination exceeded its safety limit; saved data was kept.')

    def observe(self, next_cursor, has_items):
        self.pages += 1
        self.empty_pages = 0 if has_items else self.empty_pages + 1
        if self.empty_pages >= self.empty_limit:
            raise PaginationStopped('Civitai returned repeated empty pages; saved data was kept.')
        if next_cursor is not None:
            if type(next_cursor) not in (str, int) or str(next_cursor) in self.seen:
                raise PaginationStopped('Civitai pagination stopped advancing; saved data was kept.')
            self.seen.add(str(next_cursor))
