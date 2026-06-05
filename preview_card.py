"""
Quick preview renderer — generates preview_card.png showing the new design.
Run: python preview_card.py
"""
import sys, types, io, time
sys.path.insert(0, ".")

# ── Minimal discord stub ──────────────────────────────────────────────────────
def _mod(name):
    m = types.ModuleType(name); return m

discord_stub  = _mod("discord")
ext_stub      = _mod("discord.ext")
commands_stub = _mod("discord.ext.commands")
appcmd_stub   = _mod("discord.app_commands")
tasks_stub    = _mod("discord.ext.tasks")

class _Base: pass
class _Cog(_Base):
    @staticmethod
    def listener(): return lambda f: f
commands_stub.Cog  = _Cog
commands_stub.Bot  = _Base

# ButtonStyle enum stub
class _ButtonStyle:
    success = 1
    primary = 2
    danger  = 4
    secondary = 2

class _UI: pass
discord_stub.ui          = _UI
discord_stub.ButtonStyle = _ButtonStyle
_UI.View               = _Base
_UI.Button             = _Base
_UI.button             = lambda **kw: (lambda f: f)

class _Color:
    @staticmethod
    def from_rgb(*a): return None
    red = green = light_grey = blurple = orange = staticmethod(lambda: None)

discord_stub.Color        = _Color
discord_stub.Guild        = _Base
discord_stub.Member       = _Base
discord_stub.VoiceState   = _Base
discord_stub.TextChannel  = _Base
discord_stub.Interaction  = _Base

class _Range:
    def __class_getitem__(cls, item): return int
appcmd_stub.command  = lambda **kw: (lambda f: f)
appcmd_stub.describe = lambda **kw: (lambda f: f)
appcmd_stub.Range    = _Range

sys.modules.update({
    "discord":               discord_stub,
    "discord.ext":           ext_stub,
    "discord.ext.commands":  commands_stub,
    "discord.ext.tasks":     tasks_stub,
    "discord.app_commands":  appcmd_stub,
})
discord_stub.ext = ext_stub
ext_stub.commands = commands_stub

# ── Import our real module ───────────────────────────────────────────────────
from cogs.pomodoro import PomodoroTimer, build_card
from PIL import Image, ImageDraw

# ── Fake objects ────────────────────────────────────────────────────────────
class FakeVC:
    id   = 111
    name = "study-vc-3"
    members = []

class FakeCh:
    id   = 222
    name = "general"

class FakeMember:
    def __init__(self, n): self.display_name = n

class FakeGuild:
    id = 999
    _names = {1: "AlphaStudier", 2: "BetaFocus", 3: "GammaGrind", 4: "DeltaWork"}
    def get_member(self, uid):
        n = self._names.get(uid)
        return FakeMember(n) if n else None

# ── Build timer in FOCUS state ────────────────────────────────────────────────
timer = PomodoroTimer(
    guild_id             = 999,
    voice_channel        = FakeVC(),
    notification_channel = FakeCh(),
    focus_length         = 25,
    break_length         = 5,
    name                 = "study-vc-3",
    video_required       = True,
    inactive_threshold   = 5,
)
timer.state          = "focus"
timer.time_left      = 23 * 60 + 14    # 23:14
timer.current_cycle  = 2
timer.session_seconds = {1: 6300, 2: 4580, 3: 3020, 4: 890}

# ── Fake avatars (solid colour circles) ──────────────────────────────────────
def avatar(colour):
    img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([0, 0, 127, 127], fill=colour)
    return img

avatars = {
    1: avatar((180, 80,  80)),
    2: avatar((80,  180, 120)),
    3: avatar((80,  120, 200)),
    4: avatar((180, 140,  60)),
}

# ── Render ────────────────────────────────────────────────────────────────────
buf = build_card(timer, avatars, FakeGuild())
with open("preview_card.png", "wb") as f:
    f.write(buf.read())
print("✅  preview_card.png saved!")

# Also render BREAK state
timer.state     = "break"
timer.time_left = 4 * 60 + 30
buf2 = build_card(timer, avatars, FakeGuild())
with open("preview_card_break.png", "wb") as f:
    f.write(buf2.read())
print("✅  preview_card_break.png saved!")

# Also render IDLE state
timer.state     = "idle"
timer.time_left = 0
buf3 = build_card(timer, avatars, FakeGuild())
with open("preview_card_idle.png", "wb") as f:
    f.write(buf3.read())
print("✅  preview_card_idle.png saved!")
