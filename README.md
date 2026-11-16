# 🏆 1st Place, Imperial AI Hack: Pokerbots

**Think you can beat a tournament-winning poker bot? Sit down and try.**

## ▶️ [Play it now: jumpstreetpokerbot.fly.dev](https://jumpstreetpokerbot.fly.dev)

This is the bot that won **first place** at the Imperial AI Hack Pokerbots competition. Pick a username,
play a 50-hand match of heads-up no-limit Texas hold'em against it, and see where you land on the public
leaderboard.

---

## The competition

Imperial AI Hack Pokerbots challenged teams to build autonomous agents that play no-limit Texas hold'em.
Once the tournament started there was no human input: the bots played each other on their own and were ranked
by the chips they won. This bot finished **1st**.

## The bot's code isn't here

The winning bot's source is **deliberately kept private**, but you can play against the real thing:

- The bot runs only on the game server. Your browser never receives its code, its reasoning, or its cards,
  except when a hand reaches showdown, just like at a real table.
- The server deals every card and checks every move, so the game is fair for both sides.

If you came looking for the strategy: sorry! Playing it is the closest you'll get. 🙂

## How a match works

| | |
|---|---|
| Game | Heads-up no-limit Texas hold'em: you vs the bot |
| Blinds | 10 / 20 |
| Stacks | 2,000 chips (100 big blinds), reset every hand |
| Match length | 50 hands, with the dealer button alternating |
| Your score | Big blinds won or lost over the match |

- **No account needed.** Choose a username and your browser remembers it. Nobody else can use your name while
  you're playing.
- **Only finished matches count.** If you quit, or leave a match idle for 10 minutes, it won't go on the
  leaderboard.
- **The leaderboard** shows each player's best match, plus the bot's overall record against everyone.

Fair warning: the bot was built for the competition's format, and here it's playing one person, heads-up,
for 50 hands. It still doesn't like losing.

## Tip

- Use the **½ Pot**, **Pot** and **All-in** buttons to size bets quickly. On a keyboard, `F` folds, `C`
  checks or calls, and `R` raises.
- The **History** panel shows every hand of your match, including the bot's cards whenever you saw a showdown.
- 50 hands goes fast: a full match takes about 10–15 minutes.

---

<sub>The code for the website that hosts the bot is in [`site/`](site), and how it works is in
[`docs/WEBSITE.md`](docs/WEBSITE.md).</sub>
