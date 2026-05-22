# nesemu4k.py
# AC'S NES EMU 0.1
# Python 3.14 single-file starter NES/Famicom emulator shell
# GUI: FCEUX-inspired, black background, blue text/buttons, 600x400, 60 FPS

import math
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox

APP_TITLE = "nesemu4k"
FILES_ENABLED = True
WIDTH, HEIGHT = 600, 400
NES_W, NES_H = 256, 240
FPS = 60
CPU_CYCLES_PER_FRAME = 29780
CPU_CYCLES_PER_SCANLINE = 114
VISIBLE_SCANLINES = 240
VBLANK_SCANLINES = 20

BG = "#000000"
BLUE = "#1e90ff"
DARK_BLUE = "#003366"
WHITE = "#ffffff"

# NES master palette (64 colors × RGB)
NES_PALETTE_BYTES = bytes(
    c for rgb in (
        (84, 84, 84), (0, 30, 116), (8, 16, 144), (48, 0, 136),
        (68, 0, 100), (92, 0, 48), (84, 4, 0), (60, 24, 0),
        (32, 42, 0), (8, 58, 0), (0, 64, 0), (0, 60, 0),
        (0, 50, 60), (0, 0, 0), (0, 0, 0), (0, 0, 0),
        (152, 150, 152), (8, 76, 196), (48, 50, 236), (92, 30, 228),
        (136, 20, 176), (160, 20, 100), (152, 34, 32), (120, 60, 0),
        (84, 90, 0), (40, 114, 0), (8, 124, 0), (0, 118, 40),
        (0, 102, 120), (0, 0, 0), (0, 0, 0), (0, 0, 0),
        (236, 238, 236), (76, 154, 236), (120, 124, 236), (176, 98, 236),
        (228, 84, 236), (236, 88, 180), (236, 106, 100), (212, 136, 32),
        (160, 170, 0), (116, 196, 0), (76, 208, 32), (56, 204, 108),
        (56, 180, 204), (60, 60, 60), (0, 0, 0), (0, 0, 0),
        (236, 238, 236), (168, 204, 236), (188, 188, 236), (212, 178, 236),
        (236, 174, 236), (236, 174, 212), (236, 180, 176), (228, 196, 144),
        (204, 210, 120), (180, 222, 120), (168, 226, 144), (152, 226, 180),
        (160, 214, 228), (160, 162, 160), (0, 0, 0), (0, 0, 0),
    )
    for c in rgb
)


def require_python_314():
    if sys.version_info < (3, 14):
        raise RuntimeError("nesemu4k.py requires Python 3.14 or newer.")


class NESCartridge:
    def __init__(self):
        self.prg = bytearray()
        self.chr = bytearray()
        self.mapper = 0
        self.mirror = 0
        self.valid = False
        self.name = "No ROM"
        self.prg_banks = 1
        self.is_chr_ram = False
        self.four_screen = False

    def load_ines(self, path):
        with open(path, "rb") as f:
            data = f.read()

        if len(data) < 16 or data[:4] != b"NES\x1a":
            raise ValueError("Not a valid iNES ROM.")

        prg_banks = data[4]
        chr_banks = data[5]
        flag6 = data[6]
        flag7 = data[7]

        self.mapper = (flag6 >> 4) | (flag7 & 0xF0)
        self.mirror = flag6 & 1

        has_trainer = bool(flag6 & 0x04)
        pos = 16 + (512 if has_trainer else 0)

        prg_size = prg_banks * 16384
        chr_size = chr_banks * 8192

        if prg_banks == 0 or prg_size == 0:
            raise ValueError("Invalid iNES header: PRG bank count is zero.")
        if pos + prg_size > len(data):
            raise ValueError("ROM file is truncated (PRG data shorter than header).")

        self.prg = bytearray(data[pos:pos + prg_size])
        pos += prg_size

        if chr_size > 0:
            self.chr = bytearray(data[pos:pos + chr_size])
            self.is_chr_ram = False
        else:
            self.chr = bytearray(8192)
            self.is_chr_ram = True

        self.prg_banks = max(1, len(self.prg) // 16384)
        self.four_screen = bool(flag6 & 0x08)
        self.name = os.path.basename(path)
        self.valid = True


class Mapper:
    """Mapper 0 (NROM) — fixed PRG/CHR."""

    has_scanline_irq = False
    irq_pending = False

    def __init__(self, cart):
        self.cart = cart

    def cpu_read(self, addr):
        prg = self.cart.prg
        if addr < 0xC000:
            return prg[(addr - 0x8000) % len(prg)]
        if self.cart.prg_banks == 1:
            return prg[(addr - 0xC000) % 0x4000]
        return prg[(self.cart.prg_banks - 1) * 16384 + (addr - 0xC000)]

    def cpu_write(self, addr, value):
        pass

    def ppu_read(self, addr):
        chr_ = self.cart.chr
        if addr < len(chr_):
            return chr_[addr]
        return 0

    def ppu_write(self, addr, value):
        if self.cart.is_chr_ram and addr < len(self.cart.chr):
            self.cart.chr[addr] = value & 0xFF

    def mirroring(self):
        if self.cart.four_screen:
            return 4
        return 1 if self.cart.mirror else 0


class MapperUxROM(Mapper):
    """Mapper 2."""

    def __init__(self, cart):
        super().__init__(cart)
        self.prg_bank = 0

    def cpu_read(self, addr):
        prg = self.cart.prg
        nb = self.cart.prg_banks
        if addr < 0xC000:
            return prg[(self.prg_bank & (nb - 1)) * 16384 + (addr - 0x8000)]
        return prg[(nb - 1) * 16384 + (addr - 0xC000)]

    def cpu_write(self, addr, value):
        if addr >= 0x8000:
            self.prg_bank = value & 0x0F


class MapperCNROM(Mapper):
    """Mapper 3 — banked CHR."""

    def __init__(self, cart):
        super().__init__(cart)
        self.chr_bank = 0

    def cpu_write(self, addr, value):
        if addr >= 0x8000:
            self.chr_bank = value & 0x03

    def ppu_read(self, addr):
        chr_ = self.cart.chr
        if not chr_:
            return 0
        base = (self.chr_bank * 8192) % max(1, len(chr_))
        return chr_[(base + addr) % len(chr_)]


class MapperAxROM(Mapper):
    """Mapper 7."""

    def __init__(self, cart):
        super().__init__(cart)
        self.prg_bank = 0
        self.ss_mirror = 0

    def cpu_read(self, addr):
        prg = self.cart.prg
        nb = max(1, len(prg) // 32768)
        bank = self.prg_bank & (nb - 1)
        return prg[bank * 32768 + (addr - 0x8000)]

    def cpu_write(self, addr, value):
        if addr >= 0x8000:
            self.prg_bank = value & 0x07
            self.ss_mirror = (value >> 4) & 1

    def mirroring(self):
        return 2 if self.ss_mirror == 0 else 3


class MapperMMC1(Mapper):
    """Mapper 1 — SMB, Zelda, Metroid, etc."""

    def __init__(self, cart):
        super().__init__(cart)
        self.shift = 0
        self.count = 0
        self.ctrl = 0x0C
        self.chr0 = 0
        self.chr1 = 0
        self.prg_bank = 0
        self.prg_ram = bytearray(0x2000)

    def _prg_mode(self):
        return (self.ctrl >> 2) & 3

    def _chr_mode(self):
        return (self.ctrl >> 4) & 1

    def cpu_read(self, addr):
        if 0x6000 <= addr < 0x8000:
            return self.prg_ram[addr - 0x6000]
        prg = self.cart.prg
        nb = self.cart.prg_banks
        bank = self.prg_bank & 0x0F
        mode = self._prg_mode()
        if mode <= 1:
            real = bank & 0xFE
            if addr < 0xC000:
                return prg[(real % nb) * 16384 + (addr - 0x8000)]
            return prg[((real + 1) % nb) * 16384 + (addr - 0xC000)]
        if mode == 2:
            if addr < 0xC000:
                return prg[(addr - 0x8000)]
            return prg[(bank % nb) * 16384 + (addr - 0xC000)]
        if addr < 0xC000:
            return prg[(bank % nb) * 16384 + (addr - 0x8000)]
        return prg[(nb - 1) * 16384 + (addr - 0xC000)]

    def cpu_write(self, addr, value):
        if 0x6000 <= addr < 0x8000:
            self.prg_ram[addr - 0x6000] = value & 0xFF
            return
        if addr < 0x8000:
            return
        if value & 0x80:
            self.shift = 0
            self.count = 0
            self.ctrl |= 0x0C
            return
        self.shift = (self.shift >> 1) | ((value & 1) << 4)
        self.count += 1
        if self.count == 5:
            target = (addr >> 13) & 3
            if target == 0:
                self.ctrl = self.shift
            elif target == 1:
                self.chr0 = self.shift
            elif target == 2:
                self.chr1 = self.shift
            else:
                self.prg_bank = self.shift
            self.shift = 0
            self.count = 0

    def ppu_read(self, addr):
        chr_ = self.cart.chr
        if not chr_:
            return 0
        if self._chr_mode() == 0:
            base = (self.chr0 & 0x1E) * 4096
            return chr_[(base + addr) % len(chr_)]
        if addr < 0x1000:
            base = (self.chr0 & 0x1F) * 4096
            return chr_[(base + addr) % len(chr_)]
        base = (self.chr1 & 0x1F) * 4096
        return chr_[(base + (addr - 0x1000)) % len(chr_)]

    def ppu_write(self, addr, value):
        if self.cart.is_chr_ram and addr < len(self.cart.chr):
            self.cart.chr[addr] = value & 0xFF

    def mirroring(self):
        m = self.ctrl & 3
        if m == 0:
            return 2
        if m == 1:
            return 3
        if m == 2:
            return 1
        return 0


class MapperMMC3(Mapper):
    """Mapper 4 — MMC3 (SMB3, Mega Man, Kirby, etc.)."""

    has_scanline_irq = True

    def __init__(self, cart):
        super().__init__(cart)
        self.bank_select = 0
        self.regs = [0, 2, 4, 5, 6, 7, 0, 1]
        self.prg_mode = 0
        self.chr_mode = 0
        self.mirror_mode = 0
        self.prg_ram = bytearray(0x2000)
        self.prg_ram_write_enable = True
        self.irq_latch = 0
        self.irq_counter = 0
        self.irq_reload = False
        self.irq_enable = False
        self.irq_pending = False

    def _prg_bank(self, slot):
        n8k = max(1, self.cart.prg_banks * 2)
        last = n8k - 1
        second_last = max(0, n8k - 2)
        r6 = self.regs[6] & (n8k - 1)
        r7 = self.regs[7] & (n8k - 1)
        if self.prg_mode == 0:
            return (r6, r7, second_last, last)[slot]
        return (second_last, r7, r6, last)[slot]

    def cpu_read(self, addr):
        if 0x6000 <= addr < 0x8000:
            return self.prg_ram[addr - 0x6000]
        if addr < 0x8000:
            return 0
        slot = (addr - 0x8000) >> 13
        bank = self._prg_bank(slot)
        idx = bank * 0x2000 + (addr & 0x1FFF)
        prg = self.cart.prg
        return prg[idx] if idx < len(prg) else 0

    def cpu_write(self, addr, value):
        if 0x6000 <= addr < 0x8000:
            if self.prg_ram_write_enable:
                self.prg_ram[addr - 0x6000] = value & 0xFF
            return
        if addr < 0x8000:
            return
        even = (addr & 1) == 0
        if addr < 0xA000:
            if even:
                self.bank_select = value
                self.prg_mode = (value >> 6) & 1
                self.chr_mode = (value >> 7) & 1
            else:
                self.regs[self.bank_select & 7] = value & 0xFF
        elif addr < 0xC000:
            if even:
                self.mirror_mode = value & 1
            else:
                self.prg_ram_write_enable = (value & 0x40) == 0
        elif addr < 0xE000:
            if even:
                self.irq_latch = value & 0xFF
            else:
                self.irq_reload = True
                self.irq_counter = 0
        else:
            if even:
                self.irq_enable = False
                self.irq_pending = False
            else:
                self.irq_enable = True

    def _chr_addr(self, addr):
        chr_ = self.cart.chr
        if not chr_:
            return 0
        n1k = max(1, len(chr_) // 1024)
        window = addr >> 10
        if self.chr_mode == 0:
            if window == 0:
                phys = self.regs[0] & 0xFE
            elif window == 1:
                phys = (self.regs[0] & 0xFE) | 1
            elif window == 2:
                phys = self.regs[1] & 0xFE
            elif window == 3:
                phys = (self.regs[1] & 0xFE) | 1
            else:
                phys = self.regs[2 + (window - 4)]
        else:
            if window < 4:
                phys = self.regs[2 + window]
            elif window == 4:
                phys = self.regs[0] & 0xFE
            elif window == 5:
                phys = (self.regs[0] & 0xFE) | 1
            elif window == 6:
                phys = self.regs[1] & 0xFE
            else:
                phys = (self.regs[1] & 0xFE) | 1
        phys &= n1k - 1
        return phys * 1024 + (addr & 0x3FF)

    def ppu_read(self, addr):
        chr_ = self.cart.chr
        if not chr_:
            return 0
        return chr_[self._chr_addr(addr) % len(chr_)]

    def ppu_write(self, addr, value):
        if self.cart.is_chr_ram:
            chr_ = self.cart.chr
            chr_[self._chr_addr(addr) % len(chr_)] = value & 0xFF

    def mirroring(self):
        return 0 if self.mirror_mode == 1 else 1

    def notify_scanline(self):
        if self.irq_counter == 0 or self.irq_reload:
            self.irq_counter = self.irq_latch
            self.irq_reload = False
        else:
            self.irq_counter = (self.irq_counter - 1) & 0xFF
        if self.irq_counter == 0 and self.irq_enable:
            self.irq_pending = True


def make_mapper(cart):
    m = cart.mapper
    if m == 1:
        return MapperMMC1(cart)
    if m == 2:
        return MapperUxROM(cart)
    if m == 3:
        return MapperCNROM(cart)
    if m == 4:
        return MapperMMC3(cart)
    if m == 7:
        return MapperAxROM(cart)
    return Mapper(cart)


class PPU:
    """Functional PPU: VRAM, palettes, OAM, background + sprite blit."""

    def __init__(self, bus):
        self.bus = bus
        self.vram = bytearray(0x800)
        self.palette = bytearray(0x20)
        self.oam = bytearray(256)
        self.ctrl = 0
        self.mask = 0
        self.status = 0
        self.oam_addr = 0
        self.v = 0
        self.t = 0
        self.x_fine = 0
        self.w = 0
        self.read_buffer = 0
        self.vblank = False
        self.framebuf = bytearray(NES_W * NES_H * 3)

    def reset(self):
        self.ctrl = 0
        self.mask = 0
        self.status = 0
        self.oam_addr = 0
        self.v = self.t = 0
        self.x_fine = 0
        self.w = 0
        self.read_buffer = 0
        self.vblank = False
        # Universal background + default bg/sprite palettes (until the game writes $3Fxx)
        self.palette[:] = bytes([
            0x0F, 0x30, 0x10, 0x00,
            0x0F, 0x15, 0x2C, 0x00,
            0x0F, 0x37, 0x17, 0x00,
            0x0F, 0x37, 0x17, 0x00,
            0x0F, 0x30, 0x21, 0x0F,
            0x0F, 0x27, 0x17, 0x0F,
            0x0F, 0x30, 0x10, 0x00,
            0x0F, 0x30, 0x10, 0x00,
        ])

    def cpu_read(self, addr):
        reg = 0x2000 | (addr & 7)
        if reg == 0x2002:
            val = self.status & 0xE0
            if self.vblank:
                val |= 0x80
            self.vblank = False
            self.status &= 0x7F
            self.w = 0
            return val
        if reg == 0x2004:
            return self.oam[self.oam_addr]
        if reg == 0x2007:
            v = self.v & 0x3FFF
            if v < 0x3F00:
                ret = self.read_buffer
                self.read_buffer = self._vram_read(v)
            else:
                ret = self._palette_read(v)
                self.read_buffer = self._vram_read(v - 0x1000)
            self.v = (self.v + self._vram_inc()) & 0x7FFF
            return ret
        return 0

    def cpu_write(self, addr, value):
        reg = 0x2000 | (addr & 7)
        value &= 0xFF
        if reg == 0x2000:
            self.ctrl = value
            self.t = (self.t & 0xF3FF) | ((value & 3) << 10)
        elif reg == 0x2001:
            self.mask = value
        elif reg == 0x2003:
            self.oam_addr = value
        elif reg == 0x2004:
            self.oam[self.oam_addr] = value
            self.oam_addr = (self.oam_addr + 1) & 0xFF
        elif reg == 0x2005:
            if self.w == 0:
                self.t = (self.t & 0xFFE0) | (value >> 3)
                self.x_fine = value & 7
                self.w = 1
            else:
                self.t = (self.t & 0x8FFF) | ((value & 7) << 12)
                self.t = (self.t & 0xFC1F) | ((value & 0xF8) << 2)
                self.w = 0
        elif reg == 0x2006:
            if self.w == 0:
                self.t = (self.t & 0x80FF) | ((value & 0x3F) << 8)
                self.w = 1
            else:
                self.t = (self.t & 0xFF00) | value
                self.v = self.t
                self.w = 0
        elif reg == 0x2007:
            v = self.v & 0x3FFF
            if v >= 0x3F00:
                self._palette_write(v, value)
            else:
                self._vram_write(v, value)
            self.v = (self.v + self._vram_inc()) & 0x7FFF

    def _vram_inc(self):
        return 32 if (self.ctrl & 0x04) else 1

    def _mirror_nametable(self, addr):
        addr &= 0x0FFF
        mapper = self.bus.mapper
        mode = mapper.mirroring() if mapper else 0
        if mode == 4:
            return addr & 0x7FF
        if mode == 0:  # horizontal
            if (addr & 0x400) == (addr & 0x800):
                return addr & 0x3FF
            return 0x400 | (addr & 0x3FF)
        if mode == 1:  # vertical
            return addr & 0x7FF
        if mode == 2:
            return addr & 0x3FF
        if mode == 3:
            return 0x400 | (addr & 0x3FF)
        return addr & 0x7FF

    def _vram_read(self, addr):
        addr &= 0x3FFF
        if addr < 0x2000:
            if self.bus.mapper:
                return self.bus.mapper.ppu_read(addr)
            return 0
        if addr < 0x3F00:
            return self.vram[self._mirror_nametable(addr - 0x2000)]
        return self._palette_read(addr)

    def _vram_write(self, addr, value):
        addr &= 0x3FFF
        value &= 0xFF
        if addr < 0x2000:
            if self.bus.mapper:
                self.bus.mapper.ppu_write(addr, value)
        elif addr < 0x3F00:
            self.vram[self._mirror_nametable(addr - 0x2000)] = value
        else:
            self._palette_write(addr, value)

    def _palette_index(self, addr):
        idx = addr & 0x1F
        if idx in (0x10, 0x14, 0x18, 0x1C):
            idx -= 0x10
        return idx

    def _palette_read(self, addr):
        return self.palette[self._palette_index(addr)] & 0x3F

    def _palette_write(self, addr, value):
        self.palette[self._palette_index(addr)] = value & 0x3F

    def enter_vblank(self):
        self.vblank = True
        self.status |= 0x80
        if self.ctrl & 0x80:
            self.bus.cpu.trigger_nmi()

    def exit_vblank(self):
        self.vblank = False
        self.status &= ~0x80

    def render_frame(self):
        bg_base = 0x1000 if (self.ctrl & 0x10) else 0
        spr_base = 0x1000 if (self.ctrl & 0x08) else 0
        sprite_size = 1 if (self.ctrl & 0x20) else 0
        nt_select = self.ctrl & 0x03
        base_nt = 0x2000 | (nt_select << 10)
        tiles = bytearray(960)
        for i in range(960):
            tiles[i] = self.vram[self._mirror_nametable((base_nt + i) - 0x2000)]
        attrs = bytearray(64)
        for i in range(64):
            attrs[i] = self.vram[self._mirror_nametable((base_nt + 0x3C0 + i) - 0x2000)]
        chr_view = self._chr_snapshot()
        if any(tiles):
            self._blit_bg(chr_view, tiles, attrs, bg_base)
        else:
            self._blit_chr_preview(chr_view)
        self._blit_sprites(chr_view, spr_base, sprite_size)
        return bytes(self.framebuf)

    def _blit_chr_preview(self, chr_view):
        """Fallback tile sheet when the game has not built a nametable yet."""
        pal = self.palette
        npal = NES_PALETTE_BYTES
        fb = self.framebuf
        num_tiles = max(1, len(chr_view) // 16)
        for y in range(240):
            tile_y = y >> 3
            py = y & 7
            for x in range(256):
                tile_x = x >> 3
                px = x & 7
                tile_index = (tile_y * 32 + tile_x) % num_tiles
                base = tile_index * 16 + py
                if base + 8 >= len(chr_view):
                    color_id = 0
                else:
                    lo = chr_view[base]
                    hi = chr_view[base + 8]
                    bit = 7 - px
                    color_id = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                pal_index = pal[color_id] if color_id == 0 else pal[color_id]
                m = (pal_index & 0x3F) * 3
                off = (y * 256 + x) * 3
                fb[off] = npal[m]
                fb[off + 1] = npal[m + 1]
                fb[off + 2] = npal[m + 2]

    def _chr_snapshot(self):
        if not self.bus.mapper:
            return bytes(8192)
        chr_ = bytearray(8192)
        for i in range(8192):
            chr_[i] = self.bus.mapper.ppu_read(i)
        return bytes(chr_)

    def _clear(self, pal_index):
        master = (pal_index & 0x3F) * 3
        r, g, b = NES_PALETTE_BYTES[master : master + 3]
        fb = self.framebuf
        for i in range(0, len(fb), 3):
            fb[i] = r
            fb[i + 1] = g
            fb[i + 2] = b

    def _scroll_offsets(self):
        """Coarse/fine scroll from latched $2005 (t + x_fine), matching pre-render PPU state."""
        t = self.t
        coarse_x = t & 0x1F
        coarse_y = (t >> 5) & 0x1F
        fine_y = (t >> 12) & 7
        return coarse_x * 8 + self.x_fine, coarse_y * 8 + fine_y

    def _nt_read(self, base_nt, tile_x, tile_y):
        """Tile index at nametable coords with mirroring (SMB1 uses vertical)."""
        addr = (base_nt - 0x2000) + tile_y * 32 + tile_x
        return self.vram[self._mirror_nametable(addr)]

    def _attr_read(self, base_nt, tile_x, tile_y):
        ax = tile_x >> 2
        ay = min(tile_y >> 2, 7)
        addr = (base_nt - 0x2000) + 0x3C0 + ay * 8 + ax
        return self.vram[self._mirror_nametable(addr)]

    def _blit_bg(self, chr_view, tiles, attrs, bg_base):
        fb = self.framebuf
        pal = self.palette
        npal = NES_PALETTE_BYTES
        scroll_x, scroll_y = self._scroll_offsets()
        base_nt = 0x2000 | ((self.t >> 10) & 0x03) << 10
        show_left = (self.mask & 0x02) != 0

        for sy in range(240):
            for sx in range(256):
                if sx < 8 and not show_left:
                    pal_index = pal[0]
                else:
                    world_x = scroll_x + sx
                    world_y = scroll_y + sy
                    tile_x = (world_x >> 3) & 0x1F
                    tile_y = world_y >> 3
                    py = world_y & 7
                    px = world_x & 7
                    tile_index = self._nt_read(base_nt, tile_x, tile_y)
                    base = bg_base + tile_index * 16 + py
                    if base + 8 >= len(chr_view):
                        lo = hi = 0
                    else:
                        lo = chr_view[base]
                        hi = chr_view[base + 8]
                    bit = 7 - px
                    color_id = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                    attr_byte = self._attr_read(base_nt, tile_x, tile_y)
                    shift = ((tile_y & 2) << 1) | (tile_x & 2)
                    palette_sel = (attr_byte >> shift) & 3
                    if color_id == 0:
                        pal_index = pal[0]
                    else:
                        pal_index = pal[palette_sel * 4 + color_id]
                m = (pal_index & 0x3F) * 3
                off = (sy * 256 + sx) * 3
                fb[off] = npal[m]
                fb[off + 1] = npal[m + 1]
                fb[off + 2] = npal[m + 2]

    def _blit_sprites(self, chr_view, spr_base, sprite_size):
        fb = self.framebuf
        pal = self.palette
        npal = NES_PALETTE_BYTES
        oam = self.oam
        height = 16 if sprite_size else 8
        for i in range(63, -1, -1):
            sy = oam[i * 4] + 1
            raw_tile = oam[i * 4 + 1]
            attr = oam[i * 4 + 2]
            sx = oam[i * 4 + 3]
            if sy >= 240:
                continue
            flip_h = (attr >> 6) & 1
            flip_v = (attr >> 7) & 1
            palette_sel = attr & 3
            for py in range(height):
                local_y = (height - 1 - py) if flip_v else py
                if sprite_size:
                    table = (raw_tile & 1) * 0x1000
                    top_tile = raw_tile & 0xFE
                    if local_y < 8:
                        base = table + top_tile * 16 + local_y
                    else:
                        base = table + (top_tile + 1) * 16 + (local_y - 8)
                else:
                    base = spr_base + raw_tile * 16 + local_y
                if base + 8 >= len(chr_view):
                    continue
                lo = chr_view[base]
                hi = chr_view[base + 8]
                for px in range(8):
                    screen_x = sx + px
                    screen_y = sy + py
                    if screen_x >= 256 or screen_y >= 240:
                        continue
                    bit = px if flip_h else (7 - px)
                    color_id = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                    if color_id == 0:
                        continue
                    pal_index = pal[16 + palette_sel * 4 + color_id]
                    m = (pal_index & 0x3F) * 3
                    off = (screen_y * 256 + screen_x) * 3
                    fb[off] = npal[m]
                    fb[off + 1] = npal[m + 1]
                    fb[off + 2] = npal[m + 2]


class CPU6502:
    """Full official 6502 (well, the NES 2A03 subset — no decimal mode).

    Uses a dispatch dict built once at class init. Memory access is routed
    through `bus.cpu_read` / `bus.cpu_write` so the bus can keep timing
    of the PPU/APU in lockstep later. The step() return value is cycle
    count, which the bus uses to budget frames.
    """

    def __init__(self, nes):
        self.nes = bus
        self.a = self.x = self.y = 0
        self.sp = 0xFD
        self.pc = 0x8000
        self.p = 0x20 | 0x04
        self.cycles = 0
        self.running = True
        self.nmi_pending = False
        self.irq_pending = False

    # ------------------------------------------------------------
    def reset(self) -> None:
        self.a = self.x = self.y = 0
        self.sp = 0xFD
        self.p = 0x20 | 0x04
        lo = self.read(0xFFFC)
        hi = self.read(0xFFFD)
        vec = (hi << 8) | lo
        self.pc = vec if vec else 0x8000
        self.cycles = 7
        self.running = True
        self.nmi_pending = False
        self.irq_pending = False

    # memory primitives -------------------------------------------
    def read(self, addr: int) -> int:
        return self.nes.cpu_read(addr & 0xFFFF)

    def write(self, addr: int, value: int) -> None:
        self.nes.cpu_write(addr & 0xFFFF, value & 0xFF)

    def fetch8(self) -> int:
        v = self.read(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def fetch16(self) -> int:
        lo = self.fetch8()
        hi = self.fetch8()
        return lo | (hi << 8)

    def push(self, v: int) -> None:
        self.write(0x100 | self.sp, v)
        self.sp = (self.sp - 1) & 0xFF

    def pull(self) -> int:
        self.sp = (self.sp + 1) & 0xFF
        return self.read(0x100 | self.sp)

    # flag helpers ------------------------------------------------
    def set_zn(self, v: int) -> None:
        v &= 0xFF
        if v == 0:
            self.p |= 0x02
        else:
            self.p &= ~0x02
        if v & 0x80:
            self.p |= 0x80
        else:
            self.p &= ~0x80

    def set_flag(self, flag: int, cond: bool) -> None:
        if cond:
            self.p |= flag
        else:
            self.p &= ~flag

    # interrupt entry --------------------------------------------
    def trigger_nmi(self) -> None:
        self.nmi_pending = True

    def _service_nmi(self) -> int:
        self.push((self.pc >> 8) & 0xFF)
        self.push(self.pc & 0xFF)
        self.push((self.p & ~0x10) | 0x20)
        self.p |= 0x04
        lo = self.read(0xFFFA)
        hi = self.read(0xFFFB)
        self.pc = (hi << 8) | lo
        self.nmi_pending = False
        return 7

    def _service_irq(self) -> int:
        self.push((self.pc >> 8) & 0xFF)
        self.push(self.pc & 0xFF)
        self.push((self.p & ~0x10) | 0x20)
        self.p |= 0x04
        lo = self.read(0xFFFE)
        hi = self.read(0xFFFF)
        self.pc = (hi << 8) | lo
        self.irq_pending = False
        return 7

    # addressing modes (return resolved address) -----------------
    def _addr_zp(self) -> int:
        return self.fetch8()

    def _addr_zpx(self) -> int:
        return (self.fetch8() + self.x) & 0xFF

    def _addr_zpy(self) -> int:
        return (self.fetch8() + self.y) & 0xFF

    def _addr_abs(self) -> int:
        return self.fetch16()

    def _addr_abx(self) -> int:
        return (self.fetch16() + self.x) & 0xFFFF

    def _addr_aby(self) -> int:
        return (self.fetch16() + self.y) & 0xFFFF

    def _addr_izx(self) -> int:
        base = (self.fetch8() + self.x) & 0xFF
        lo = self.read(base)
        hi = self.read((base + 1) & 0xFF)
        return (lo | (hi << 8)) & 0xFFFF

    def _addr_izy(self) -> int:
        base = self.fetch8()
        lo = self.read(base)
        hi = self.read((base + 1) & 0xFF)
        return ((lo | (hi << 8)) + self.y) & 0xFFFF

    # ALU helpers ------------------------------------------------
    def _adc(self, v: int) -> None:
        c = self.p & 0x01
        total = self.a + v + c
        self.set_flag(0x01, total > 0xFF)
        result = total & 0xFF
        self.set_flag(0x40, bool((~(self.a ^ v) & (self.a ^ result)) & 0x80))
        self.a = result
        self.set_zn(self.a)

    def _sbc(self, v: int) -> None:
        self._adc(v ^ 0xFF)

    def _cmp_reg(self, reg: int, v: int) -> None:
        r = (reg - v) & 0xFF
        self.set_flag(0x01, reg >= v)
        self.set_zn(r)

    def _branch_if(self, cond: bool) -> int:
        off = self.fetch8()
        if off & 0x80:
            off -= 0x100
        if cond:
            old = self.pc
            self.pc = (self.pc + off) & 0xFFFF
            return 3 + (1 if (old & 0xFF00) != (self.pc & 0xFF00) else 0)
        return 2

    def _asl(self, v: int) -> int:
        self.set_flag(0x01, v & 0x80)
        v = (v << 1) & 0xFF
        self.set_zn(v)
        return v

    def _lsr(self, v: int) -> int:
        self.set_flag(0x01, v & 1)
        v >>= 1
        self.set_zn(v)
        return v

    def _rol(self, v: int) -> int:
        c = self.p & 0x01
        self.set_flag(0x01, v & 0x80)
        v = ((v << 1) | c) & 0xFF
        self.set_zn(v)
        return v

    def _ror(self, v: int) -> int:
        c = self.p & 0x01
        self.set_flag(0x01, v & 1)
        v = (v >> 1) | (c << 7)
        self.set_zn(v)
        return v

    # main step --------------------------------------------------
    def step(self) -> int:
        if self.nmi_pending:
            return self._service_nmi()
        if self.irq_pending and not (self.p & 0x04):
            return self._service_irq()
        if not self.running:
            return 2

        op = self.fetch8()

        # === LDA family ===
        if op == 0xA9: self.a = self.fetch8();           self.set_zn(self.a); return 2
        if op == 0xA5: self.a = self.read(self._addr_zp());  self.set_zn(self.a); return 3
        if op == 0xB5: self.a = self.read(self._addr_zpx()); self.set_zn(self.a); return 4
        if op == 0xAD: self.a = self.read(self._addr_abs()); self.set_zn(self.a); return 4
        if op == 0xBD: self.a = self.read(self._addr_abx()); self.set_zn(self.a); return 4
        if op == 0xB9: self.a = self.read(self._addr_aby()); self.set_zn(self.a); return 4
        if op == 0xA1: self.a = self.read(self._addr_izx()); self.set_zn(self.a); return 6
        if op == 0xB1: self.a = self.read(self._addr_izy()); self.set_zn(self.a); return 5

        # === LDX ===
        if op == 0xA2: self.x = self.fetch8();           self.set_zn(self.x); return 2
        if op == 0xA6: self.x = self.read(self._addr_zp());  self.set_zn(self.x); return 3
        if op == 0xB6: self.x = self.read(self._addr_zpy()); self.set_zn(self.x); return 4
        if op == 0xAE: self.x = self.read(self._addr_abs()); self.set_zn(self.x); return 4
        if op == 0xBE: self.x = self.read(self._addr_aby()); self.set_zn(self.x); return 4

        # === LDY ===
        if op == 0xA0: self.y = self.fetch8();           self.set_zn(self.y); return 2
        if op == 0xA4: self.y = self.read(self._addr_zp());  self.set_zn(self.y); return 3
        if op == 0xB4: self.y = self.read(self._addr_zpx()); self.set_zn(self.y); return 4
        if op == 0xAC: self.y = self.read(self._addr_abs()); self.set_zn(self.y); return 4
        if op == 0xBC: self.y = self.read(self._addr_abx()); self.set_zn(self.y); return 4

        # === STA ===
        if op == 0x85: self.write(self._addr_zp(),  self.a); return 3
        if op == 0x95: self.write(self._addr_zpx(), self.a); return 4
        if op == 0x8D: self.write(self._addr_abs(), self.a); return 4
        if op == 0x9D: self.write(self._addr_abx(), self.a); return 5
        if op == 0x99: self.write(self._addr_aby(), self.a); return 5
        if op == 0x81: self.write(self._addr_izx(), self.a); return 6
        if op == 0x91: self.write(self._addr_izy(), self.a); return 6

        # === STX / STY ===
        if op == 0x86: self.write(self._addr_zp(),  self.x); return 3
        if op == 0x96: self.write(self._addr_zpy(), self.x); return 4
        if op == 0x8E: self.write(self._addr_abs(), self.x); return 4
        if op == 0x84: self.write(self._addr_zp(),  self.y); return 3
        if op == 0x94: self.write(self._addr_zpx(), self.y); return 4
        if op == 0x8C: self.write(self._addr_abs(), self.y); return 4

        # === register transfers ===
        if op == 0xAA: self.x = self.a;  self.set_zn(self.x); return 2  # TAX
        if op == 0xA8: self.y = self.a;  self.set_zn(self.y); return 2  # TAY
        if op == 0x8A: self.a = self.x;  self.set_zn(self.a); return 2  # TXA
        if op == 0x98: self.a = self.y;  self.set_zn(self.a); return 2  # TYA
        if op == 0xBA: self.x = self.sp; self.set_zn(self.x); return 2  # TSX
        if op == 0x9A: self.sp = self.x;                          return 2  # TXS

        # === INC/DEC ===
        if op == 0xE8: self.x = (self.x + 1) & 0xFF; self.set_zn(self.x); return 2
        if op == 0xC8: self.y = (self.y + 1) & 0xFF; self.set_zn(self.y); return 2
        if op == 0xCA: self.x = (self.x - 1) & 0xFF; self.set_zn(self.x); return 2
        if op == 0x88: self.y = (self.y - 1) & 0xFF; self.set_zn(self.y); return 2
        if op in (0xE6, 0xF6, 0xEE, 0xFE):  # INC zp / zp,X / abs / abs,X
            addr = (self._addr_zp() if op == 0xE6 else
                    self._addr_zpx() if op == 0xF6 else
                    self._addr_abs() if op == 0xEE else
                    self._addr_abx())
            v = (self.read(addr) + 1) & 0xFF
            self.write(addr, v); self.set_zn(v)
            return 5 if op in (0xE6,) else (6 if op in (0xF6, 0xEE) else 7)
        if op in (0xC6, 0xD6, 0xCE, 0xDE):  # DEC zp / zp,X / abs / abs,X
            addr = (self._addr_zp() if op == 0xC6 else
                    self._addr_zpx() if op == 0xD6 else
                    self._addr_abs() if op == 0xCE else
                    self._addr_abx())
            v = (self.read(addr) - 1) & 0xFF
            self.write(addr, v); self.set_zn(v)
            return 5 if op in (0xC6,) else (6 if op in (0xD6, 0xCE) else 7)

        # === ADC / SBC ===
        if op == 0x69: self._adc(self.fetch8());                return 2
        if op == 0x65: self._adc(self.read(self._addr_zp()));   return 3
        if op == 0x75: self._adc(self.read(self._addr_zpx()));  return 4
        if op == 0x6D: self._adc(self.read(self._addr_abs()));  return 4
        if op == 0x7D: self._adc(self.read(self._addr_abx()));  return 4
        if op == 0x79: self._adc(self.read(self._addr_aby()));  return 4
        if op == 0x61: self._adc(self.read(self._addr_izx()));  return 6
        if op == 0x71: self._adc(self.read(self._addr_izy()));  return 5
        if op == 0xE9 or op == 0xEB: self._sbc(self.fetch8());  return 2  # 0xEB = unofficial SBC
        if op == 0xE5: self._sbc(self.read(self._addr_zp()));   return 3
        if op == 0xF5: self._sbc(self.read(self._addr_zpx()));  return 4
        if op == 0xED: self._sbc(self.read(self._addr_abs()));  return 4
        if op == 0xFD: self._sbc(self.read(self._addr_abx()));  return 4
        if op == 0xF9: self._sbc(self.read(self._addr_aby()));  return 4
        if op == 0xE1: self._sbc(self.read(self._addr_izx()));  return 6
        if op == 0xF1: self._sbc(self.read(self._addr_izy()));  return 5

        # === AND / ORA / EOR ===
        if op == 0x29: self.a &= self.fetch8();           self.set_zn(self.a); return 2
        if op == 0x25: self.a &= self.read(self._addr_zp());  self.set_zn(self.a); return 3
        if op == 0x35: self.a &= self.read(self._addr_zpx()); self.set_zn(self.a); return 4
        if op == 0x2D: self.a &= self.read(self._addr_abs()); self.set_zn(self.a); return 4
        if op == 0x3D: self.a &= self.read(self._addr_abx()); self.set_zn(self.a); return 4
        if op == 0x39: self.a &= self.read(self._addr_aby()); self.set_zn(self.a); return 4
        if op == 0x21: self.a &= self.read(self._addr_izx()); self.set_zn(self.a); return 6
        if op == 0x31: self.a &= self.read(self._addr_izy()); self.set_zn(self.a); return 5
        if op == 0x09: self.a |= self.fetch8();           self.set_zn(self.a); return 2
        if op == 0x05: self.a |= self.read(self._addr_zp());  self.set_zn(self.a); return 3
        if op == 0x15: self.a |= self.read(self._addr_zpx()); self.set_zn(self.a); return 4
        if op == 0x0D: self.a |= self.read(self._addr_abs()); self.set_zn(self.a); return 4
        if op == 0x1D: self.a |= self.read(self._addr_abx()); self.set_zn(self.a); return 4
        if op == 0x19: self.a |= self.read(self._addr_aby()); self.set_zn(self.a); return 4
        if op == 0x01: self.a |= self.read(self._addr_izx()); self.set_zn(self.a); return 6
        if op == 0x11: self.a |= self.read(self._addr_izy()); self.set_zn(self.a); return 5
        if op == 0x49: self.a ^= self.fetch8();           self.set_zn(self.a); return 2
        if op == 0x45: self.a ^= self.read(self._addr_zp());  self.set_zn(self.a); return 3
        if op == 0x55: self.a ^= self.read(self._addr_zpx()); self.set_zn(self.a); return 4
        if op == 0x4D: self.a ^= self.read(self._addr_abs()); self.set_zn(self.a); return 4
        if op == 0x5D: self.a ^= self.read(self._addr_abx()); self.set_zn(self.a); return 4
        if op == 0x59: self.a ^= self.read(self._addr_aby()); self.set_zn(self.a); return 4
        if op == 0x41: self.a ^= self.read(self._addr_izx()); self.set_zn(self.a); return 6
        if op == 0x51: self.a ^= self.read(self._addr_izy()); self.set_zn(self.a); return 5

        # === CMP / CPX / CPY ===
        if op == 0xC9: self._cmp_reg(self.a, self.fetch8());           return 2
        if op == 0xC5: self._cmp_reg(self.a, self.read(self._addr_zp())); return 3
        if op == 0xD5: self._cmp_reg(self.a, self.read(self._addr_zpx())); return 4
        if op == 0xCD: self._cmp_reg(self.a, self.read(self._addr_abs())); return 4
        if op == 0xDD: self._cmp_reg(self.a, self.read(self._addr_abx())); return 4
        if op == 0xD9: self._cmp_reg(self.a, self.read(self._addr_aby())); return 4
        if op == 0xC1: self._cmp_reg(self.a, self.read(self._addr_izx())); return 6
        if op == 0xD1: self._cmp_reg(self.a, self.read(self._addr_izy())); return 5
        if op == 0xE0: self._cmp_reg(self.x, self.fetch8());           return 2
        if op == 0xE4: self._cmp_reg(self.x, self.read(self._addr_zp())); return 3
        if op == 0xEC: self._cmp_reg(self.x, self.read(self._addr_abs())); return 4
        if op == 0xC0: self._cmp_reg(self.y, self.fetch8());           return 2
        if op == 0xC4: self._cmp_reg(self.y, self.read(self._addr_zp())); return 3
        if op == 0xCC: self._cmp_reg(self.y, self.read(self._addr_abs())); return 4

        # === shifts / rotates - A and memory ===
        if op == 0x0A: self.a = self._asl(self.a); return 2
        if op == 0x4A: self.a = self._lsr(self.a); return 2
        if op == 0x2A: self.a = self._rol(self.a); return 2
        if op == 0x6A: self.a = self._ror(self.a); return 2
        if op in (0x06, 0x16, 0x0E, 0x1E):  # ASL mem
            addr = (self._addr_zp() if op == 0x06 else
                    self._addr_zpx() if op == 0x16 else
                    self._addr_abs() if op == 0x0E else
                    self._addr_abx())
            self.write(addr, self._asl(self.read(addr)))
            return 5 if op == 0x06 else (6 if op in (0x16, 0x0E) else 7)
        if op in (0x46, 0x56, 0x4E, 0x5E):
            addr = (self._addr_zp() if op == 0x46 else
                    self._addr_zpx() if op == 0x56 else
                    self._addr_abs() if op == 0x4E else
                    self._addr_abx())
            self.write(addr, self._lsr(self.read(addr)))
            return 5 if op == 0x46 else (6 if op in (0x56, 0x4E) else 7)
        if op in (0x26, 0x36, 0x2E, 0x3E):
            addr = (self._addr_zp() if op == 0x26 else
                    self._addr_zpx() if op == 0x36 else
                    self._addr_abs() if op == 0x2E else
                    self._addr_abx())
            self.write(addr, self._rol(self.read(addr)))
            return 5 if op == 0x26 else (6 if op in (0x36, 0x2E) else 7)
        if op in (0x66, 0x76, 0x6E, 0x7E):
            addr = (self._addr_zp() if op == 0x66 else
                    self._addr_zpx() if op == 0x76 else
                    self._addr_abs() if op == 0x6E else
                    self._addr_abx())
            self.write(addr, self._ror(self.read(addr)))
            return 5 if op == 0x66 else (6 if op in (0x76, 0x6E) else 7)

        # === BIT ===
        if op == 0x24:
            v = self.read(self._addr_zp())
            self.p = (self.p & 0x3F) | (v & 0xC0)
            self.set_flag(0x02, (self.a & v) == 0)
            # set_flag(Z) handled, but set_zn also touches N — do manually:
            return 3
        if op == 0x2C:
            v = self.read(self._addr_abs())
            self.p = (self.p & 0x3F) | (v & 0xC0)
            self.set_flag(0x02, (self.a & v) == 0)
            return 4

        # === jumps ===
        if op == 0x4C: self.pc = self._addr_abs(); return 3
        if op == 0x6C:                            # JMP (ind), with the 6502 page-wrap bug
            ptr = self.fetch16()
            lo = self.read(ptr)
            hi = self.read((ptr & 0xFF00) | ((ptr + 1) & 0xFF))
            self.pc = lo | (hi << 8); return 5
        if op == 0x20:                            # JSR abs
            addr = self.fetch16()
            ret = (self.pc - 1) & 0xFFFF
            self.push((ret >> 8) & 0xFF)
            self.push(ret & 0xFF)
            self.pc = addr
            return 6
        if op == 0x60:                            # RTS
            lo = self.pull(); hi = self.pull()
            self.pc = ((hi << 8) | lo) + 1
            self.pc &= 0xFFFF
            return 6
        if op == 0x40:                            # RTI
            self.p = (self.pull() & ~0x10) | 0x20
            lo = self.pull(); hi = self.pull()
            self.pc = (hi << 8) | lo
            return 6

        # === branches ===
        if op == 0xD0: return self._branch_if(not (self.p & 0x02))  # BNE
        if op == 0xF0: return self._branch_if(bool(self.p & 0x02))  # BEQ
        if op == 0x90: return self._branch_if(not (self.p & 0x01))  # BCC
        if op == 0xB0: return self._branch_if(bool(self.p & 0x01))  # BCS
        if op == 0x10: return self._branch_if(not (self.p & 0x80))  # BPL
        if op == 0x30: return self._branch_if(bool(self.p & 0x80))  # BMI
        if op == 0x50: return self._branch_if(not (self.p & 0x40))  # BVC
        if op == 0x70: return self._branch_if(bool(self.p & 0x40))  # BVS

        # === flags ===
        if op == 0x18: self.p &= ~0x01; return 2  # CLC
        if op == 0x38: self.p |= 0x01;  return 2  # SEC
        if op == 0x58: self.p &= ~0x04; return 2  # CLI
        if op == 0x78: self.p |= 0x04;  return 2  # SEI
        if op == 0xB8: self.p &= ~0x40; return 2  # CLV
        if op == 0xD8: self.p &= ~0x08; return 2  # CLD
        if op == 0xF8: self.p |= 0x08;  return 2  # SED

        # === stack ===
        if op == 0x48: self.push(self.a); return 3   # PHA
        if op == 0x68: self.a = self.pull(); self.set_zn(self.a); return 4  # PLA
        if op == 0x08: self.push(self.p | 0x30); return 3        # PHP
        if op == 0x28: self.p = (self.pull() & ~0x10) | 0x20; return 4  # PLP

        # === NOP family (official + unofficial) ===
        if op == 0xEA: return 2
        # unofficial NOPs that some games hit:
        if op in (0x1A, 0x3A, 0x5A, 0x7A, 0xDA, 0xFA): return 2
        if op in (0x80, 0x82, 0x89, 0xC2, 0xE2):       # NOP #imm
            self.fetch8(); return 2
        if op in (0x04, 0x44, 0x64):                   # NOP zp
            self.fetch8(); return 3
        if op in (0x14, 0x34, 0x54, 0x74, 0xD4, 0xF4): # NOP zp,X
            self.fetch8(); return 4
        if op == 0x0C:                                 # NOP abs
            self.fetch16(); return 4
        if op in (0x1C, 0x3C, 0x5C, 0x7C, 0xDC, 0xFC): # NOP abs,X
            self.fetch16(); return 4

        # === BRK ===
        if op == 0x00:
            self.pc = (self.pc + 1) & 0xFFFF
            self.push((self.pc >> 8) & 0xFF)
            self.push(self.pc & 0xFF)
            self.push(self.p | 0x30)
            self.p |= 0x04
            lo = self.read(0xFFFE); hi = self.read(0xFFFF)
            self.pc = (hi << 8) | lo
            return 7

        # === UNOFFICIAL OPCODES ===
        # These show up in commercial games (some intentional, some
        # baked into copy-protection / Konami / Sunsoft routines). The
        # six 2A03-stable groups are LAX, SAX, DCP, ISC, SLO, RLA, SRE,
        # RRA. We implement them so games like Super Cars, Disney's
        # Aladdin, Beavis & Butthead, and various unlicensed ROMs don't
        # die on the very first fetch.

        # LAX (LDA + LDX) — load to A and X simultaneously
        if op in (0xA7, 0xB7, 0xAF, 0xBF, 0xA3, 0xB3):
            if   op == 0xA7: addr = self._addr_zp();  cyc = 3
            elif op == 0xB7: addr = self._addr_zpy(); cyc = 4
            elif op == 0xAF: addr = self._addr_abs(); cyc = 4
            elif op == 0xBF: addr = self._addr_aby(); cyc = 4
            elif op == 0xA3: addr = self._addr_izx(); cyc = 6
            else:            addr = self._addr_izy(); cyc = 5
            v = self.read(addr)
            self.a = v; self.x = v
            self.set_zn(v)
            return cyc

        # SAX (store A & X) — write the AND of A and X to memory, no flags
        if op in (0x87, 0x97, 0x8F, 0x83):
            if   op == 0x87: addr = self._addr_zp();  cyc = 3
            elif op == 0x97: addr = self._addr_zpy(); cyc = 4
            elif op == 0x8F: addr = self._addr_abs(); cyc = 4
            else:            addr = self._addr_izx(); cyc = 6
            self.write(addr, self.a & self.x)
            return cyc

        # DCP (DEC + CMP) — decrement memory then compare with A
        if op in (0xC7, 0xD7, 0xCF, 0xDF, 0xDB, 0xC3, 0xD3):
            if   op == 0xC7: addr = self._addr_zp();  cyc = 5
            elif op == 0xD7: addr = self._addr_zpx(); cyc = 6
            elif op == 0xCF: addr = self._addr_abs(); cyc = 6
            elif op == 0xDF: addr = self._addr_abx(); cyc = 7
            elif op == 0xDB: addr = self._addr_aby(); cyc = 7
            elif op == 0xC3: addr = self._addr_izx(); cyc = 8
            else:            addr = self._addr_izy(); cyc = 8
            v = (self.read(addr) - 1) & 0xFF
            self.write(addr, v)
            self._cmp_reg(self.a, v)
            return cyc

        # ISC (INC + SBC) — also called ISB. Increment memory then SBC.
        if op in (0xE7, 0xF7, 0xEF, 0xFF, 0xFB, 0xE3, 0xF3):
            if   op == 0xE7: addr = self._addr_zp();  cyc = 5
            elif op == 0xF7: addr = self._addr_zpx(); cyc = 6
            elif op == 0xEF: addr = self._addr_abs(); cyc = 6
            elif op == 0xFF: addr = self._addr_abx(); cyc = 7
            elif op == 0xFB: addr = self._addr_aby(); cyc = 7
            elif op == 0xE3: addr = self._addr_izx(); cyc = 8
            else:            addr = self._addr_izy(); cyc = 8
            v = (self.read(addr) + 1) & 0xFF
            self.write(addr, v)
            self._sbc(v)
            return cyc

        # SLO (ASL + ORA)
        if op in (0x07, 0x17, 0x0F, 0x1F, 0x1B, 0x03, 0x13):
            if   op == 0x07: addr = self._addr_zp();  cyc = 5
            elif op == 0x17: addr = self._addr_zpx(); cyc = 6
            elif op == 0x0F: addr = self._addr_abs(); cyc = 6
            elif op == 0x1F: addr = self._addr_abx(); cyc = 7
            elif op == 0x1B: addr = self._addr_aby(); cyc = 7
            elif op == 0x03: addr = self._addr_izx(); cyc = 8
            else:            addr = self._addr_izy(); cyc = 8
            v = self._asl(self.read(addr))
            self.write(addr, v)
            self.a |= v; self.set_zn(self.a)
            return cyc

        # RLA (ROL + AND)
        if op in (0x27, 0x37, 0x2F, 0x3F, 0x3B, 0x23, 0x33):
            if   op == 0x27: addr = self._addr_zp();  cyc = 5
            elif op == 0x37: addr = self._addr_zpx(); cyc = 6
            elif op == 0x2F: addr = self._addr_abs(); cyc = 6
            elif op == 0x3F: addr = self._addr_abx(); cyc = 7
            elif op == 0x3B: addr = self._addr_aby(); cyc = 7
            elif op == 0x23: addr = self._addr_izx(); cyc = 8
            else:            addr = self._addr_izy(); cyc = 8
            v = self._rol(self.read(addr))
            self.write(addr, v)
            self.a &= v; self.set_zn(self.a)
            return cyc

        # SRE (LSR + EOR)
        if op in (0x47, 0x57, 0x4F, 0x5F, 0x5B, 0x43, 0x53):
            if   op == 0x47: addr = self._addr_zp();  cyc = 5
            elif op == 0x57: addr = self._addr_zpx(); cyc = 6
            elif op == 0x4F: addr = self._addr_abs(); cyc = 6
            elif op == 0x5F: addr = self._addr_abx(); cyc = 7
            elif op == 0x5B: addr = self._addr_aby(); cyc = 7
            elif op == 0x43: addr = self._addr_izx(); cyc = 8
            else:            addr = self._addr_izy(); cyc = 8
            v = self._lsr(self.read(addr))
            self.write(addr, v)
            self.a ^= v; self.set_zn(self.a)
            return cyc

        # RRA (ROR + ADC)
        if op in (0x67, 0x77, 0x6F, 0x7F, 0x7B, 0x63, 0x73):
            if   op == 0x67: addr = self._addr_zp();  cyc = 5
            elif op == 0x77: addr = self._addr_zpx(); cyc = 6
            elif op == 0x6F: addr = self._addr_abs(); cyc = 6
            elif op == 0x7F: addr = self._addr_abx(); cyc = 7
            elif op == 0x7B: addr = self._addr_aby(); cyc = 7
            elif op == 0x63: addr = self._addr_izx(); cyc = 8
            else:            addr = self._addr_izy(); cyc = 8
            v = self._ror(self.read(addr))
            self.write(addr, v)
            self._adc(v)
            return cyc

        # ANC (AND + carry from bit 7)
        if op in (0x0B, 0x2B):
            self.a &= self.fetch8(); self.set_zn(self.a)
            self.set_flag(0x01, bool(self.a & 0x80))
            return 2
        # ALR (AND #imm then LSR A)
        if op == 0x4B:
            self.a &= self.fetch8()
            self.set_flag(0x01, self.a & 1)
            self.a >>= 1; self.set_zn(self.a)
            return 2
        # ARR (AND #imm then ROR A; weird flag behavior)
        if op == 0x6B:
            self.a &= self.fetch8()
            c = self.p & 0x01
            self.a = (self.a >> 1) | (c << 7)
            self.set_zn(self.a)
            self.set_flag(0x01, bool(self.a & 0x40))
            self.set_flag(0x40, bool(((self.a >> 6) ^ (self.a >> 5)) & 1))
            return 2
        # AXS / SBX (X = (A & X) - imm)
        if op == 0xCB:
            v = self.fetch8()
            t = (self.a & self.x) - v
            self.set_flag(0x01, t >= 0)
            self.x = t & 0xFF
            self.set_zn(self.x)
            return 2

        # Unknown opcode: silent NOP so the GUI never dies.
        return 2


# =============================================================================
#  PPU - register file + nametable resolution + delegation to blitter
# =============================================================================

class APU:
    """Frame-counter IRQ so commercial games do not hang on boot."""

    def __init__(self, bus):
        self.bus = bus
        self.frame_mode = 0
        self.frame_irq_inhibit = False
        self.frame_irq = False
        self._cycles = 0

    def reset(self):
        self.frame_mode = 0
        self.frame_irq_inhibit = False
        self.frame_irq = False
        self._cycles = 0

    def cpu_read(self, addr):
        if addr == 0x4015:
            v = 0
            if self.frame_irq:
                v |= 0x40
                self.frame_irq = False
            return v
        return 0

    def cpu_write(self, addr, value):
        if addr == 0x4017:
            self.frame_mode = (value >> 7) & 1
            self.frame_irq_inhibit = bool(value & 0x40)
            if self.frame_irq_inhibit:
                self.frame_irq = False
            self._cycles = 0

    def tick(self, cycles):
        self._cycles += cycles
        period = 37281 if self.frame_mode else 29830
        while self._cycles >= period:
            self._cycles -= period
            if self.frame_mode == 0 and not self.frame_irq_inhibit:
                self.frame_irq = True
                self.bus.cpu.irq_pending = True


class ACNES:
    def __init__(self):
        self.cart = NESCartridge()
        self.mapper = None
        self.cpu_ram = bytearray(2048)
        self.controller = 0
        self.controller_strobe = False
        self.controller_shift = 0
        self.cpu = CPU6502(self)
        self.ppu = PPU(self)
        self.apu = APU(self)
        self.frame = 0

    def reset(self):
        self.cpu_ram = bytearray(2048)
        self.mapper = make_mapper(self.cart) if self.cart.valid else None
        self.controller = 0
        self.controller_strobe = False
        self.controller_shift = 0
        self.frame = 0
        self.ppu.reset()
        self.apu.reset()
        self.cpu.reset()

    def cpu_read(self, addr):
        addr &= 0xFFFF

        if addr < 0x2000:
            return self.cpu_ram[addr & 0x07FF]

        if addr < 0x4000:
            return self.ppu.cpu_read(addr)

        if addr == 0x4015:
            return self.apu.cpu_read(addr)
        if addr == 0x4016:
            if self.controller_strobe:
                return self.controller & 1
            bit = self.controller_shift & 1
            self.controller_shift = (self.controller_shift >> 1) | 0x80
            return bit
        if addr == 0x4017:
            return self.apu.cpu_read(addr)
        if 0x4000 <= addr <= 0x4017:
            return 0

        if addr >= 0x8000 and self.mapper:
            return self.mapper.cpu_read(addr)

        return 0

    def cpu_write(self, addr, value):
        addr &= 0xFFFF
        value &= 0xFF

        if addr < 0x2000:
            self.cpu_ram[addr & 0x07FF] = value
        elif addr < 0x4000:
            self.ppu.cpu_write(addr, value)
        elif addr == 0x4014:
            base = (value & 0xFF) << 8
            for i in range(256):
                self.ppu.oam[(self.ppu.oam_addr + i) & 0xFF] = self.cpu_read(base + i)
            self.cpu.cycles += 513
        elif addr == 0x4016:
            if value & 1:
                self.controller_strobe = True
                self.controller_shift = self.controller
            else:
                self.controller_strobe = False
                self.controller_shift = self.controller
        elif 0x4000 <= addr <= 0x4017:
            self.apu.cpu_write(addr, value)
        elif addr >= 0x8000 and self.mapper:
            self.mapper.cpu_write(addr, value)

    def run_frame(self):
        ppu = self.ppu
        cpu = self.cpu
        apu = self.apu
        mapper = self.mapper
        step = cpu.step
        cycles_sl = CPU_CYCLES_PER_SCANLINE
        total_visible = CPU_CYCLES_PER_SCANLINE * VISIBLE_SCANLINES

        if mapper and getattr(mapper, "has_scanline_irq", False):
            rendering = (ppu.mask & 0x18) != 0
            for sl in range(VISIBLE_SCANLINES):
                used = 0
                while used < cycles_sl and cpu.running:
                    used += step()
                apu.tick(used)
                if rendering:
                    mapper.notify_scanline()
                if (mapper.irq_pending or cpu.irq_pending) and not (cpu.p & 0x04):
                    cpu._service_irq()
                    mapper.irq_pending = False
            ppu.enter_vblank()
            for _ in range(VBLANK_SCANLINES + 1):
                used = 0
                while used < cycles_sl and cpu.running:
                    used += step()
                apu.tick(used)
                if cpu.irq_pending and not (cpu.p & 0x04):
                    cpu._service_irq()
            ppu.exit_vblank()
            ppu.status &= ~0x40
            self.frame += 1
            return

        used = 0
        while used < total_visible and cpu.running:
            used += step()
        apu.tick(used)
        ppu.enter_vblank()
        vb_target = (VBLANK_SCANLINES + 1) * cycles_sl
        vb = 0
        while vb < vb_target and cpu.running:
            vb += step()
            if cpu.irq_pending and not (cpu.p & 0x04):
                vb += cpu._service_irq()
        apu.tick(vb)
        ppu.exit_vblank()
        ppu.status &= ~0x40
        self.frame += 1

    def warmup(self, frames=90):
        for _ in range(frames):
            self.run_frame()

    def render_pixels(self):
        if not self.cart.valid:
            pixels = []
            t = self.frame * 0.06
            for y in range(NES_H):
                row = []
                for x in range(NES_W):
                    wave = int((math.sin(x * 0.05 + t) + math.cos(y * 0.05 + t)) * 40 + 80)
                    if (x // 16 + y // 16 + self.frame // 20) % 2 == 0:
                        row.append(
                            (
                                0,
                                max(0, min(255, 40 + wave)),
                                max(0, min(255, 120 + wave)),
                            )
                        )
                    else:
                        row.append((0, 0, 20))
                pixels.append(row)
            return pixels

        fb = self.ppu.render_frame()
        pixels = []
        for y in range(NES_H):
            off = y * NES_W * 3
            pixels.append(
                [(fb[off + x * 3], fb[off + x * 3 + 1], fb[off + x * 3 + 2]) for x in range(NES_W)]
            )
        return pixels


class FCEUXStyleGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(f"{WIDTH}x{HEIGHT}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        self.nes = ACNES()
        self.paused = False
        self.last_time = time.perf_counter()
        self.frames = 0
        self.fps_text = "60"

        self.make_menu()
        self.make_layout()

        self.photo = tk.PhotoImage(width=NES_W, height=NES_H)
        self.screen_img = self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

        self.bind_keys()
        self.root.after_idle(lambda: self.root.focus_force())
        self.loop()

    def make_menu(self):
        menubar = tk.Menu(self.root, bg=BG, fg=BLUE, activebackground=DARK_BLUE, activeforeground=WHITE)
        filemenu = tk.Menu(menubar, tearoff=0, bg=BG, fg=BLUE, activebackground=DARK_BLUE, activeforeground=WHITE)
        filemenu.add_command(label="Open ROM", command=self.open_rom)
        filemenu.add_command(label="Reset", command=self.reset)
        filemenu.add_separator()
        filemenu.add_command(label="Exit", command=self.root.destroy)

        emumenu = tk.Menu(menubar, tearoff=0, bg=BG, fg=BLUE, activebackground=DARK_BLUE, activeforeground=WHITE)
        emumenu.add_command(label="Pause/Resume", command=self.toggle_pause)
        emumenu.add_command(label="About", command=self.about)

        menubar.add_cascade(label="File", menu=filemenu)
        menubar.add_cascade(label="Emulation", menu=emumenu)
        self.root.config(menu=menubar)

    def button(self, parent, text, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BG,
            fg=BLUE,
            activebackground=DARK_BLUE,
            activeforeground=WHITE,
            relief="ridge",
            bd=2,
            width=12,
        )

    def make_layout(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x")

        title = tk.Label(
            top,
            text="NESEMU4K  |  NES/FAMICOM 60 FPS",
            bg=BG,
            fg=BLUE,
            font=("Consolas", 12, "bold"),
        )
        title.pack(side="left", padx=8, pady=4)

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            main,
            width=NES_W,
            height=NES_H,
            bg=BG,
            highlightthickness=2,
            highlightbackground=BLUE,
        )
        self.canvas.pack(side="left", padx=10, pady=8)

        side = tk.Frame(main, bg=BG)
        side.pack(side="right", fill="y", padx=8, pady=8)

        self.button(side, "Open ROM", self.open_rom).pack(pady=4)
        self.button(side, "Reset", self.reset).pack(pady=4)
        self.button(side, "Pause", self.toggle_pause).pack(pady=4)
        self.button(side, "About", self.about).pack(pady=4)
        self.button(side, "Exit", self.root.destroy).pack(pady=4)

        self.status = tk.Label(
            side,
            text="ROM: none\nMapper: N/A\nMode: demo\nFPS: 60",
            bg=BG,
            fg=BLUE,
            justify="left",
            font=("Consolas", 9),
        )
        self.status.pack(pady=12)

        controls = tk.Label(
            self.root,
            text="Controls: Arrow Keys = D-Pad | Z = A | X = B | Enter = Start | Right Shift = Select",
            bg=BG,
            fg=BLUE,
            font=("Consolas", 9),
        )
        controls.pack(side="bottom", pady=4)

    def bind_keys(self):
        self.root.bind("<KeyPress>", self.key_down)
        self.root.bind("<KeyRelease>", self.key_up)

    def key_bit(self, event):
        k = event.keysym.lower()
        if k == "z":
            return 0
        if k == "x":
            return 1
        if k == "shift_r":
            return 2
        if k == "return":
            return 3
        if k == "up":
            return 4
        if k == "down":
            return 5
        if k == "left":
            return 6
        if k == "right":
            return 7
        return None

    def key_down(self, event):
        bit = self.key_bit(event)
        if bit is not None:
            self.nes.controller |= 1 << bit

    def key_up(self, event):
        bit = self.key_bit(event)
        if bit is not None:
            self.nes.controller &= ~(1 << bit)

    def open_rom(self):
        if not FILES_ENABLED:
            messagebox.showinfo(
                APP_TITLE,
                "Files are OFF in this build.\n\n"
                "ROM loading is disabled, so acnes4k runs in built-in demo mode at 60 FPS."
            )
            return

        path = filedialog.askopenfilename(
            title="Open NES ROM",
            filetypes=[
                ("NES ROM", "*.nes"),
                ("iNES ROM", "*.nes"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        try:
            self.nes.cart.load_ines(path)
            self.nes.reset()
            self.nes.warmup(120)
            self.paused = False
            self.update_status()
        except Exception as e:
            messagebox.showerror("ROM Load Error", str(e))

    def reset(self):
        self.nes.reset()
        self.paused = False
        self.update_status()

    def toggle_pause(self):
        self.paused = not self.paused
        self.update_status()

    def about(self):
        messagebox.showinfo(
            APP_TITLE,
            "nesemu4k — NES/Famicom emulator\n"
            "Python 3.14 single-file build.\n\n"
            "Open any iNES (.nes) ROM. Mappers: 0,1,2,3,4,7 (+ NROM fallback).\n"
            "Full 6502, PPU (bg+sprites), APU frame IRQ, MMC3 scanline IRQ.\n"
            "GUI: FCEUX-inspired black/blue."
        )

    def update_status(self):
        cart = self.nes.cart
        mode = "paused" if self.paused else ("running" if cart.valid else "demo")
        mapper = cart.mapper if cart.valid else "N/A"
        self.status.config(
            text=f"ROM: {cart.name}\nMapper: {mapper}\nMode: {mode}\nFPS: {self.fps_text}"
        )

    def draw_pixels(self, pixels):
        # Fast enough for this small starter emulator.
        lines = []
        for row in pixels:
            line = "{" + " ".join(f"#{r:02x}{g:02x}{b:02x}" for r, g, b in row) + "}"
            lines.append(line)
        self.photo.put(" ".join(lines), to=(0, 0))

    def loop(self):
        start = time.perf_counter()

        if not self.paused:
            if self.nes.cart.valid:
                self.nes.run_frame()
            else:
                self.nes.frame += 1

        pixels = self.nes.render_pixels()
        self.draw_pixels(pixels)

        self.frames += 1
        now = time.perf_counter()
        if now - self.last_time >= 1.0:
            self.fps_text = str(self.frames)
            self.frames = 0
            self.last_time = now
            self.update_status()

        elapsed = time.perf_counter() - start
        delay_ms = max(1, int((1.0 / FPS - elapsed) * 1000))
        self.root.after(delay_ms, self.loop)


def main():
    require_python_314()
    root = tk.Tk()
    FCEUXStyleGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
