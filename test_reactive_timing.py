#!/usr/bin/env python3
"""Simple test to verify state_decide queues DTMF in pending_dtmf instead of returning it."""

import sys
from reactive_engine import ReactiveState, state_decide

def test_card_number_trigger():
    """Test that card number prompt queues DTMF instead of returning it."""
    state = ReactiveState()
    card_number = "1234567890123456"
    security_code = "123"
    
    # Simulate IVR asking for card number
    transcript = "Please enter or say your card number"
    result = state_decide(state, transcript, card_number, security_code)
    
    # Verify: should return None, not the digits
    assert result is None, f"Expected None, got {result}"
    
    # Verify: pending_dtmf should be set
    expected_digits = card_number + "#"
    assert state.pending_dtmf == expected_digits, f"Expected pending_dtmf={expected_digits}, got {state.pending_dtmf}"
    
    # Verify: last_change_ts should be set
    assert state.last_change_ts > 0.0, "Expected last_change_ts to be set"
    
    print("✓ Card number trigger test passed")
    print(f"  - state_decide returned: {result}")
    print(f"  - pending_dtmf: {state.pending_dtmf}")
    print(f"  - last_change_ts: {state.last_change_ts}")

def test_english_trigger():
    """Test that English prompt queues DTMF."""
    state = ReactiveState()
    card_number = "1234567890123456"
    security_code = "123"
    
    # Simulate IVR asking for language selection
    transcript = "To continue in English, press 1"
    result = state_decide(state, transcript, card_number, security_code)
    
    assert result is None, f"Expected None, got {result}"
    assert state.pending_dtmf == "1", f"Expected pending_dtmf=1, got {state.pending_dtmf}"
    assert state.last_change_ts > 0.0, "Expected last_change_ts to be set"
    
    print("✓ English trigger test passed")
    print(f"  - state_decide returned: {result}")
    print(f"  - pending_dtmf: {state.pending_dtmf}")

def test_cvv_trigger():
    """Test that CVV prompt queues DTMF."""
    state = ReactiveState()
    state.triggered.add("english_1")  # Already pressed 1
    state.triggered.add("card_number")  # Already sent card
    card_number = "1234567890123456"
    security_code = "123"
    
    # Simulate IVR asking for CVV
    transcript = "Please enter your three digit security code"
    result = state_decide(state, transcript, card_number, security_code)
    
    assert result is None, f"Expected None, got {result}"
    assert state.pending_dtmf == security_code, f"Expected pending_dtmf={security_code}, got {state.pending_dtmf}"
    assert state.last_change_ts > 0.0, "Expected last_change_ts to be set"
    
    print("✓ CVV trigger test passed")
    print(f"  - state_decide returned: {result}")
    print(f"  - pending_dtmf: {state.pending_dtmf}")

if __name__ == "__main__":
    try:
        test_english_trigger()
        print()
        test_card_number_trigger()
        print()
        test_cvv_trigger()
        print()
        print("All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"✗ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
