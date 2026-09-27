# Demo recording for the README, driven by vhs (https://github.com/charmbracelet/vhs):
#
#     vhs docs/demo.tape        # writes docs/demo.gif
#
# Prerequisites: `python` on PATH with mcpdump installed. The built-in server is
# launched as `python -m mcpdump.demo`, so a PATH python that is not the
# interpreter mcpdump is installed into fails the whole recording; confirm with
# `mcpdump demo --cmd` first.
#
# The README currently embeds docs/demo.svg, which is generated offline by
# `python docs/make_demo_svg.py`. Regenerating a GIF with this tape is the
# higher-fidelity option once vhs is available.

Output docs/demo.gif

Set FontSize 14
Set Width 900
Set Height 620
Set Padding 16
Set Margin 0
Set Theme "Catppuccin Mocha"
Set TypingSpeed 55ms
Set Framerate 30

# 1) What does this server expose?
Type "mcpdump ls 'python -m mcpdump.demo'"
Enter
Sleep 4s

# 2) The raw JSON-RPC frames — the reason this tool exists
Type "mcpdump call 'python -m mcpdump.demo' echo --args '{\"text\":\"hello\"}'"
Enter
Sleep 4s

# 3) Everything on one screen
Type "mcpdump tui 'python -m mcpdump.demo'"
Enter
Sleep 3s

# Move to the wire pane and scroll
Tab
Sleep 400ms
Down
Sleep 300ms
Up
Sleep 300ms

# Filter the tool list, then clear it
Type "/"
Sleep 300ms
Type "ad"
Sleep 700ms
Escape
Sleep 600ms

# Quit; the last frame stays in the scrollback
Type "q"
Sleep 1s
