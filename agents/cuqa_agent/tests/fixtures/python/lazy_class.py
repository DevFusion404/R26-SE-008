"""Python fixture: LazyClass smell — tiny class, ≤ 2 methods AND < 30 lines."""


class TinyClass:
    def do_one_thing(self):
        return 42
# Only 1 method, < 30 lines → LazyClass triggered
