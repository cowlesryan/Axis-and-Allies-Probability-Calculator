"""Rule register: every rule the calculator relies on, with its source and status.

Status values (strongest first):
    ``official-faq``   stated in the official 1942 FAQ (Sept 3, 2014).
    ``comparison``     stated in the official "1942 and Revised Editions Rules
                       Comparison" sheet (written for the 1942 line; the first
                       print run, so differences are flagged where known).
    ``rulebook-copy``  stated in a third-party text copy of the 2nd Edition
                       rulebook (RulesPal); not independently verified.
    ``owner``          confirmed by the project owner.
    ``assumption``     modelling choice not settled by any source; the
                       calculator prints it when it matters.

The CLI ``rules`` command prints this table. RULES.md has the same content with
longer explanations.
"""

RULES = [
    ("stats", "all",
     "Unit costs/attack/defense: inf 3 1/2, art 4 2/2, tank 6 3/3, ftr 10 3/4, bmb 12 4/1, "
     "sub 6 2/1, des 8 2/2, cru 12 3/3, bb 20 4/4, trn 7 0/0.",
     "rulebook-copy"),
    ("aa-cost", "all", "AA gun costs 5 IPC (the comparison sheet says 6 for the first print run).", "owner"),
    ("carrier-attack", "sea", "Aircraft carrier attacks at 1 and defends at 2.", "owner"),
    ("hit-roll", "all", "A unit hits on a d6 roll <= its value; both sides fire simultaneously.", "rulebook-copy"),
    ("art-boost", "land", "Each artillery raises one infantry to attack 2.", "rulebook-copy"),
    ("aa-fire", "land", "AA gun: up to 3 shots, each attacking aircraft shot at once, hit on a 1, "
     "fired once before the first round; defends at 0.", "rulebook-copy"),
    ("aa-casualty", "land", "An AA gun may be taken as a casualty; the standard policy takes it first.",
     "assumption"),
    ("sub-surprise", "sea", "Subs strike first (attack 2 / defend 1) unless the enemy has a destroyer; "
     "struck units do not fire back.", "rulebook-copy"),
    ("sub-air", "sea", "Subs cannot hit aircraft; aircraft cannot hit subs unless their side has a destroyer "
     "in the battle.", "official-faq"),
    ("sub-destroyer", "sea", "An enemy destroyer cancels surprise strike and submerging.", "official-faq"),
    ("hits-mandatory", "sea", "All legal hits must be applied (e.g. sub hit on a destroyer and cruiser hit "
     "on a fighter in the same step).", "official-faq"),
    ("battleship", "sea", "Battleships take two hits.", "rulebook-copy"),
    ("transport", "sea", "Transports never roll, are taken as casualties last, and are removed "
     "automatically when only one side can still roll dice.", "official-faq"),
    ("fight-to-end", "all", "No retreat or submerge in this release.", "owner"),
    ("bombard", "amph", "Battleships hit on <=4, cruisers <=3, at most one ship per landing unit; "
     "bombarded defenders still fire back (1942 pre-combat step).", "comparison"),
    ("bombard-ships", "amph", "Bombarding ships do not also fight in the sea battle.", "assumption"),
    ("transport-cargo", "amph", "A transport carries 2 land units; cargo of sunk transports is lost "
     "(weakest first).", "assumption"),
    ("sea-clear", "amph", "A landing needs a cleared sea zone; a stalemate at sea means no landing.",
     "assumption"),
    ("sbr", "sbr", "Complex shoots once per bomber (hit on 1); survivors each roll d6 damage; total damage "
     "capped at 2 x territory IPC.", "rulebook-copy"),
    ("sbr-escort", "sbr", "Escorts/interceptors are not modelled.", "assumption"),
    ("policy", "all", "Casualty choice follows the chosen policy (standard / optimal / custom order).",
     "assumption"),
    ("optimal-scope", "all", "'optimal' is a best response to the other side's fixed policy, chosen after "
     "dice are known; surprise-strike, bombardment and AA casualties always follow the standard order.",
     "assumption"),
    ("bb-repair", "sea", "Battleship repair timing does not affect a single battle; not modelled.", "assumption"),
    ("carrier-planes", "sea", "Fighters based on a sunk carrier are not lost in the battle; landing is "
     "not modelled.", "assumption"),
]


def rule_lines(battle=None):
    """Formatted rule lines, optionally filtered by battle kind (land/sea/amph/sbr)."""
    return [f"[{status}] {text}" for _, kind, text, status in RULES
            if battle is None or kind in ("all", battle)]
