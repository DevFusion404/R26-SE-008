"""Python fixture: LargeClass smell — 16 methods (threshold > 15)."""


class LargeService:
    def method01(self): pass
    def method02(self): pass
    def method03(self): pass
    def method04(self): pass
    def method05(self): pass
    def method06(self): pass
    def method07(self): pass
    def method08(self): pass
    def method09(self): pass
    def method10(self): pass
    def method11(self): pass
    def method12(self): pass
    def method13(self): pass
    def method14(self): pass
    def method15(self): pass
    def method16(self): pass   # 16th method → LargeClass triggered (> 15)
