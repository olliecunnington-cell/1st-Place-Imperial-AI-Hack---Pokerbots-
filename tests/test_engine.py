import pytest

from app.engine import FULL_DECK, Hand, IllegalAction, best_hand, hand_name, settle_pots


def rank(cards: str):
    return best_hand(cards.split())


# ------------------------------------------------------------------ hand ranking

@pytest.mark.parametrize("cards,name", [
    ("As Ks Qs Js Ts 2d 3c", "Straight flush"),
    ("5h 4h 3h 2h Ah Kd Kc", "Straight flush"),          # steel wheel
    ("9c 9d 9h 9s 2d 3c 4h", "Four of a kind"),
    ("Kc Kd Kh 2s 2d 7c 8h", "Full house"),
    ("2h 7h 9h Jh Kh Ac Ad", "Flush"),
    ("5c 6d 7h 8s 9c Kd Kh", "Straight"),
    ("Ac 2d 3h 4s 5c Kd Qh", "Straight"),                # wheel
    ("7c 7d 7h Ks 2d 4c 9h", "Three of a kind"),
    ("7c 7d 9h 9s 2d 4c Jh", "Two pair"),
    ("7c 7d 9h Ks 2d 4c Jh", "One pair"),
    ("Ac Td 9h 6s 2d 4c Jh", "High card"),
])
def test_categories(cards, name):
    assert hand_name(rank(cards)) == name


@pytest.mark.parametrize("better,worse", [
    ("As Ks Qs Js Ts", "9s 8s 7s 6s 5s"),                # higher straight flush
    ("6c 5d 4h 3s 2c", "Ac 2d 3h 4s 5c"),                # six-high straight beats the wheel
    ("Ac Kd Qh Js Tc", "Kc Qd Jh Ts 9c"),
    ("2c 2d 2h 2s 3c", "Kc Kd Kh Qs Qc"),                # quads beat a full house
    ("Ac Ad Ah 2s 2c", "Kc Kd Kh As Ac"),                # trips decide a full house
    ("Kc Kd Kh As Ac", "Kc Kd Kh Qs Qc"),                # then the pair
    ("Ah Th 8h 6h 4h", "Ah Th 8h 6h 3h"),                # flush down to the fifth card
    ("Ac Ad Kh Ks 3c", "Ac Ad Qh Qs Kc"),                # top pair of two pair
    ("Ac Ad 9h 9s Kc", "Ac Ad 9h 9s Qc"),                # two-pair kicker
    ("Ac Ad Kh 9s 4c", "Ac Ad Kh 9s 3c"),                # pair, last kicker
    ("9c 9d 9h As 2c", "9c 9d 9h Ks Qc"),                # trips kicker
    ("Ac Kd 9h 7s 3c", "Ac Kd 9h 7s 2c"),
])
def test_ordering(better, worse):
    assert rank(better) > rank(worse)


def test_board_plays_tie():
    board = "Ac Kd Qh Js Tc".split()
    assert best_hand(board + ["2c", "3d"]) == best_hand(board + ["4h", "5s"])


def test_seven_cards_pick_best_five():
    # Flush beats the straight also available in these seven cards.
    assert hand_name(rank("4h 5h 6h 7d 8h Kh 2c")) == "Flush"
    # Three pairs: best two pairs with the best kicker.
    assert rank("Ac Ad Kh Ks Qc Qd 2h") == (2, 12, 11, 10)


# ------------------------------------------------------------------ pots

def test_split_pot_odd_chip_goes_left_of_button():
    v = rank("Ac Kd Qh Js Tc")
    won = settle_pots([25, 25], [False, False], [v, v], [1, 0])
    assert sorted(won) == [25, 25]
    won = settle_pots([26, 25], [False, False], [v, v], [1, 0])   # 51 chips, extra 1 returned to seat 0
    assert won == [26, 25]


def test_side_pot_short_stack_wins_main_only():
    strong, weak = rank("Ac Ad Ah Ks Qc"), rank("2c 3d 5h 7s 9c")
    # seat 1 all-in for 500, seat 0 committed 1200 (700 uncalled above seat 1)
    won = settle_pots([1200, 500], [False, False], [weak, strong], [1, 0])
    assert won == [700, 1000]


def test_side_pot_big_stack_wins_everything():
    strong, weak = rank("Ac Ad Ah Ks Qc"), rank("2c 3d 5h 7s 9c")
    won = settle_pots([1200, 500], [False, False], [strong, weak], [1, 0])
    assert won == [1700, 0]


def test_folded_seat_gets_nothing():
    won = settle_pots([60, 20], [False, True], [None, None], [1, 0])
    assert won == [80, 0]


# ------------------------------------------------------------------ hand flow helpers

def stacked_deck(p_bb, p_sb, board):
    """Deck where seat bb gets p_bb, seat sb gets p_sb, then the board, all via pop()."""
    order = [p_bb[0], p_sb[0], p_bb[1], p_sb[1]] + board
    rest = [c for c in FULL_DECK if c not in order]
    return rest + list(reversed(order))


def new_hand(button=0, stacks=(2000, 2000), deck=None):
    return Hand(stacks=list(stacks), button=button, small_blind=10, big_blind=20, deck=deck)


# ------------------------------------------------------------------ legal actions

def test_preflop_blinds_and_first_action():
    h = new_hand(button=0)
    assert h.bets == [10, 20] and h.pot == 30 and h.to_act == 0
    lg = h.legal(0)
    assert lg["fold"] and lg["call"] == 10 and "check" not in lg
    assert lg["raise"] == {"min": 40, "max": 2000, "kind": "raise"}
    assert h.legal(1) == {}


def test_out_of_turn_and_illegal_actions_rejected():
    h = new_hand(button=0)
    with pytest.raises(IllegalAction):
        h.act(1, "check")                           # not seat 1's turn
    with pytest.raises(IllegalAction):
        h.act(0, "check")                           # facing the big blind
    for bad in (39, 2001, -5, "100", 50.5, None, True):
        with pytest.raises(IllegalAction):
            h.act(0, "raise", bad)
    with pytest.raises(IllegalAction):
        h.act(0, "shove")
    assert h.history[-1]["action"] == "big_blind"   # nothing was applied


def test_big_blind_option_and_street_order():
    h = new_hand(button=0)
    h.act(0, "call")
    assert h.to_act == 1 and h.street == "preflop"
    lg = h.legal(1)
    assert lg["check"] and "fold" not in lg and lg["raise"]["min"] == 40
    h.act(1, "check")
    assert h.street == "flop" and len(h.board) == 3
    assert h.to_act == 1                            # big blind acts first after the flop
    assert h.legal(1)["raise"]["kind"] == "bet" and h.legal(1)["raise"]["min"] == 20


def test_min_raise_tracks_last_raise_size():
    h = new_hand(button=0)
    h.act(0, "raise", 100)                          # raise of 80
    assert h.legal(1)["raise"]["min"] == 180
    h.act(1, "raise", 300)                          # raise of 200
    assert h.legal(0)["raise"]["min"] == 500
    assert h.raises_this_street == 2


def test_cannot_raise_when_opponent_all_in():
    h = new_hand(button=0)
    h.act(0, "raise", 2000)
    lg = h.legal(1)
    assert "raise" not in lg and lg["call"] == 1980 and lg["fold"]


def test_short_all_in_does_not_reopen_betting():
    # Seat 0 (button) 2000, seat 1 has 450. 0 raises to 300, 1 jams 450 (raise of 150 < 280).
    h = new_hand(button=0, stacks=(2000, 450))
    h.act(0, "raise", 300)
    h.act(1, "raise", 450)
    lg = h.legal(0)
    assert "raise" not in lg and lg["call"] == 150


def test_incomplete_raise_against_unacted_player_allows_reraise_engine_rule():
    h = new_hand(button=1, stacks=(2000, 2000))
    h.act(1, "call")
    h.act(0, "check")
    # flop: seat 0 bets 100, seat 1 raises to 300; seat 0 may re-raise
    h.act(0, "raise", 100)
    h.act(1, "raise", 300)
    assert h.legal(0)["raise"]["min"] == 500


# ------------------------------------------------------------------ outcomes

def test_fold_awards_pot_and_hides_nothing_extra():
    h = new_hand(button=0)
    h.act(0, "raise", 60)
    h.act(1, "fold")
    assert h.finished and not h.result.showdown
    assert h.stacks == [2020, 1980] and h.result.net == [20, -20]


def test_all_in_runout_and_showdown():
    deck = stacked_deck(p_bb=["Ac", "Ad"], p_sb=["Kc", "Kd"], board=["2h", "7s", "9d", "3c", "4h"])
    h = new_hand(button=0, deck=deck)               # seat 1 = BB gets aces
    h.act(0, "raise", 2000)
    h.act(1, "call")
    assert h.finished and h.result.showdown and len(h.board) == 5
    assert h.stacks == [0, 4000] and h.result.net == [-2000, 2000]


def test_split_pot_hand():
    deck = stacked_deck(p_bb=["2c", "3d"], p_sb=["2h", "3s"], board=["Ac", "Kd", "Qh", "Js", "Tc"])
    h = new_hand(button=0, deck=deck)
    h.act(0, "call"); h.act(1, "check")
    for _ in range(3):
        h.act(1, "check"); h.act(0, "check")
    assert h.finished and h.result.showdown
    assert h.stacks == [2000, 2000] and h.result.net == [0, 0] and h.result.winners == [0, 1]


def test_uncalled_all_in_excess_returned():
    deck = stacked_deck(p_bb=["Ac", "Ad"], p_sb=["Kc", "Kd"], board=["2h", "7s", "9d", "3c", "4h"])
    h = new_hand(button=0, stacks=(2000, 500), deck=deck)
    h.act(0, "raise", 2000)
    h.act(1, "call")                                # calls all-in for 480 more
    assert h.history[-1] == {"seat": 1, "street": "preflop", "action": "call", "amount": 480}
    assert h.stacks == [1500, 1000]                  # seat 0 gets 1500 uncalled back, loses 500


def test_big_stack_wins_short_all_in():
    deck = stacked_deck(p_bb=["Kc", "Kd"], p_sb=["Ac", "Ad"], board=["2h", "7s", "9d", "3c", "4h"])
    h = new_hand(button=0, stacks=(2000, 500), deck=deck)
    h.act(0, "raise", 2000)
    h.act(1, "call")
    assert h.stacks == [2500, 0]


def test_blind_puts_player_all_in_runs_out():
    h = new_hand(button=0, stacks=(2000, 15))       # BB can only post 15
    assert h.bets == [10, 15] and h.to_act == 0
    lg = h.legal(0)
    assert lg["call"] == 5 and "raise" not in lg
    h.act(0, "call")
    assert h.finished and len(h.board) == 5
    assert sum(h.stacks) == 2015


def test_chips_conserved_over_random_play():
    import random
    rnd = random.Random(7)
    for _ in range(400):
        h = new_hand(button=rnd.randint(0, 1))
        while not h.finished:
            s = h.to_act
            lg = h.legal(s)
            choice = rnd.choice(list(lg))
            if choice == "raise":
                h.act(s, "raise", rnd.randint(lg["raise"]["min"], lg["raise"]["max"]))
            else:
                h.act(s, choice)
        assert sum(h.stacks) == 4000
        assert sum(h.result.net) == 0
        assert len(set(h.board + h.hole[0] + h.hole[1])) == len(h.board) + 4


def test_history_matches_bot_format():
    h = new_hand(button=0)
    h.act(0, "raise", 60)
    h.act(1, "call")
    assert h.history == [
        {"seat": 0, "street": "preflop", "action": "small_blind", "amount": 10},
        {"seat": 1, "street": "preflop", "action": "big_blind", "amount": 20},
        {"seat": 0, "street": "preflop", "action": "raise", "amount": 60},
        {"seat": 1, "street": "preflop", "action": "call", "amount": 40},
    ]
    assert h.street == "flop" and h.pot == 120
