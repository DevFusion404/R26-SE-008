"""Unit tests to verify that numeric literals assigned to constant variables (#define, const, UPPERCASE = val, final int CONST = val) are NOT flagged as MagicNumber smells."""

import pytest
from report_generator import generate_file_report


def test_python_constant_assignments_not_flagged():
    src = '''
MAX_RETRIES = 5
DEFAULT_TIMEOUT = 30
TAX_RATE: float = 0.15
self.MAX_BUFFER_SIZE = 1024

def process():
    x = 999  # Should be flagged
    if x > 50: # Should be flagged
        pass
'''
    report = generate_file_report(src, "config.py")
    smells = [s for s in report["code_smells"] if s["type"] == "MagicNumber"]
    
    # 999 and 50 should be flagged
    # 5, 30, 0.15, 1024 MUST NOT be flagged
    flagged_messages = [s["message"] for s in smells]
    assert any("999" in msg for msg in flagged_messages)
    assert any("50" in msg for msg in flagged_messages)
    
    assert not any(" 5 " in msg or " 5 " in msg for msg in flagged_messages)
    assert not any("30" in msg for msg in flagged_messages)
    assert not any("1024" in msg for msg in flagged_messages)


def test_java_constant_assignments_not_flagged():
    src = '''
public class Config {
    public static final int MAX_RETRIES = 5;
    private static final int DEFAULT_TIMEOUT = 30;
    final double TAX_RATE = 0.15;

    public void process(int x) {
        int tempVal = 999;
        if (x > 50) {
            System.out.println(tempVal);
        }
    }
}
'''
    report = generate_file_report(src, "Config.java")
    smells = [s for s in report["code_smells"] if s["type"] == "MagicNumber"]

    flagged_messages = [s["message"] for s in smells]
    assert any("999" in msg for msg in flagged_messages)
    assert any("50" in msg for msg in flagged_messages)

    assert not any("30" in msg for msg in flagged_messages)


def test_c_constant_assignments_not_flagged():
    src = '''
#define MAX_RETRIES 5
#define DEFAULT_TIMEOUT 30
const int MAX_BUFFER = 1024;

void process(int x) {
    int temp = 999;
    if (x > 50) {
        // pass
    }
}
'''
    report = generate_file_report(src, "config.c")
    smells = [s for s in report["code_smells"] if s["type"] == "MagicNumber"]

    flagged_messages = [s["message"] for s in smells]
    assert any("999" in msg for msg in flagged_messages)
    assert any("50" in msg for msg in flagged_messages)

    assert not any("30" in msg for msg in flagged_messages)
    assert not any("1024" in msg for msg in flagged_messages)
