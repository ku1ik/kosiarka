#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import getopt
import sys

def usage():
    print('Usage: ./tondeuse.py [options]')
    print('  -s, --slow=DELAY    slow delay')
    print('  -f, --fast=DELAY    fast delay')
    print('      --miss=RATE     miss per myriad')
    print('  -i, --nointerrupt   Ctrl+C is not your friend anymore')
    
try:
    opts, args = getopt.getopt(sys.argv[1:], 's:f:ih',
                               ['slow=', 'fast=', 'miss=',
                                'nointerrupt'])
except getopt.GetoptError as err:
    # print help information and exit:
    print(str(err)) # will print something like "option -a not recognized"
    usage()
    sys.exit(2)
        
slow_delay = 0.5 # time in seconds to mow a ; in slow mode (default mode)
fast_delay = 0.05 # time in seconds to mow a ; in fast mode

miss_permyriad = 0 # be a perfect mower by default
catch_sigint = False # catch sigint so that no one can stop the mowing?

for o,a in opts:
    if o in ('-s', '--slow'):
        slow_delay = float(a)
    elif o in ('-f', '--fast'):
        fast_delay = float(a)
    elif o in ('-h'):
        usage()
        sys.exit(0)
    elif o in ('--miss='):
        miss_permyriad = float(a)
    elif o in ('-i','--nointerrupt'):
        catch_sigint = True
        

import curses
from random import randint
import signal
import time

class lawn:
    def __init__(self,win,slow_delay,fast_delay,miss_permyriad):
        self.term = win
        self.slow_delay = slow_delay
        self.fast_delay = fast_delay
        self.delay = slow_delay
        self.miss_permyriad = miss_permyriad

        self.grass = ';'
        self.cut_grass = ','
        self.height_chars = ['.', ',', ';', '/']

        # Regrowth tuning (seconds).
        self.regrow_base = 25.0
        self.regrow_jitter = 7.5
        # Number of ticks to scan the whole pad once for regrowth.
        self.regrow_scan_cycles = 200

        # Color pairs (fg, bg) used throughout the animation.
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)

        self.normal_attr = curses.color_pair(1)
        # Grass is green; cut grass is a dimmer green to show it's mowed.
        self.grass_attr = curses.color_pair(2)
        self.cut_grass_attr = curses.color_pair(2) | curses.A_DIM
        # Mower motor is red and bold to stand out.
        self.motor_attr = curses.color_pair(3) | curses.A_BOLD
        # Visual levels for grass height (shortest -> tallest).
        self.height_attrs = [
            curses.color_pair(2) | curses.A_DIM,  # .
            curses.color_pair(2),                  # ,
            curses.color_pair(2) | curses.A_BOLD,  # ;
            curses.color_pair(2) | curses.A_BOLD,  # #
        ]

        (self.garden_h, self.garden_w) = self.term.getmaxyx()
        self.mower_size = 4

        self.init_lawn()

        # Track height and regrowth timing per cell.
        self.heights = [
            [3 for _ in range(self.pad_w)]
            for _ in range(self.pad_h)
        ]
        self.last_change = [
            [0.0 for _ in range(self.pad_w)]
            for _ in range(self.pad_h)
        ]
        self.regrow_delay = [
            [self.regrow_base for _ in range(self.pad_w)]
            for _ in range(self.pad_h)
        ]
        for y in range(self.pad_h):
            for x in range(self.pad_w):
                jitter_seed = (y * 1315423911 + x * 2654435761) & 0xFFFF
                jitter = (jitter_seed / 65535.0) * self.regrow_jitter
                self.regrow_delay[y][x] = self.regrow_base + jitter
        self.regrow_cursor = 0

        # paint unmowed lawn
        # Fill the whole pad with tall grass (no bold to avoid global bold).
        self.scr.bkgd(ord(self.height_chars[3]), curses.color_pair(2))
        self.refresh_screen()

        # set some curses parameters
        curses.curs_set(0) # invisible cursor

    def init_lawn(self):
        '''create a pad for drawing into and apply it on the terminal'''

        # the drawing pad is the screen
        # + room for the mower to go beyond the screen on the left and right
        # + room for one blade of cut grass (behind the mower) on each side
        self.pad_h = self.garden_h + 1
        self.pad_w = self.garden_w + 2*self.mower_size + 2
        self.scr = curses.newpad(self.pad_h, self.pad_w)

        self.scr.nodelay(True) # non-blocking getch()

    def right_mower(self,y,x):
        '''paint the mower going from left to right'''
        
        # (y,x) is the position of the blade of grass directly in front of the
        # mower, that is, directly to its right
        self.scr.addstr(y, x - 4, '`', self.normal_attr)
        self.scr.addstr(y, x - 3, '.', self.normal_attr)
        self.scr.addstr(y, x - 2, '=', self.motor_attr)
        self.scr.addstr(y, x - 1, '.', self.normal_attr)

    def left_mower(self,y,x):
        '''paint the mower going from right to left'''
        
        # (y,x) is the position of the blade of grass directly in front of the
        # mower, that is, directly to its left
        self.scr.addstr(y, x + 1, '.', self.normal_attr)
        self.scr.addstr(y, x + 2, '=', self.motor_attr)
        self.scr.addstr(y, x + 3, '.', self.normal_attr)
        self.scr.addstr(y, x + 4, '\'', self.normal_attr)

    def mow_grass(self,y,x):
        '''paint a blade of mowed grass if the mowing succeeds'''
        
        if self.miss_permyriad == 0 or randint(0,10000) > self.miss_permyriad:
            self.heights[y][x] = 0
            self.last_change[y][x] = time.time()
            self.scr.addstr(y, x, self.height_chars[0], self.height_attrs[0])
        else:
            h = self.heights[y][x]
            if h > 0:
                h = h - 1
                self.heights[y][x] = h
                self.last_change[y][x] = time.time()
                self.scr.addstr(y, x, self.height_chars[h], self.height_attrs[h])

    def regrow_grass(self, now):
        '''regrow a few cut blades per tick'''
        total = self.pad_h * self.pad_w
        if total == 0:
            return

        cells_per_tick = max(1, total // self.regrow_scan_cycles)
        for _ in range(cells_per_tick):
            idx = self.regrow_cursor
            self.regrow_cursor = (self.regrow_cursor + 1) % total
            y = idx // self.pad_w
            x = idx - y * self.pad_w
            h = self.heights[y][x]
            if h >= 3:
                continue

            if now - self.last_change[y][x] >= self.regrow_delay[y][x]:
                h = h + 1
                self.heights[y][x] = h
                self.last_change[y][x] = now
                self.scr.addstr(y, x, self.height_chars[h], self.height_attrs[h])

    def handle_events(self):
        c = self.scr.getch()
        if c == -1:
            return

        if c == ord(' '):
            if self.delay == self.slow_delay:
                self.delay = self.fast_delay
            else:
                self.delay = self.slow_delay
            self.ticks = 0
            self.t0 = time.time()

    def row_dir(self, y):
        if self.cycle_start_side == 'left':
            return 1 if y % 2 == 0 else -1
        return -1 if y % 2 == 0 else 1

    def draw_cell(self, y, x):
        if y < 0 or y >= self.pad_h or x < 0 or x >= self.pad_w:
            return
        h = self.heights[y][x]
        self.scr.addstr(y, x, self.height_chars[h], self.height_attrs[h])

    def clear_mower(self, y, x, direction):
        if direction == 1:
            coords = [(y, x - 4), (y, x - 3), (y, x - 2), (y, x - 1)]
        else:
            coords = [(y, x + 1), (y, x + 2), (y, x + 3), (y, x + 4)]
        for cy, cx in coords:
            self.draw_cell(cy, cx)

    def mow(self):
        # (self.y, self.x) is the position of the blade of grass
        # directly in front of the mower inside the pad
        (self.y, self.x) = (0, self.mower_size + 1)
        self.cycle_start_side = 'left'
        self.state = 'mowing'
        self.last_mower = None
        self.return_side = 'right'
        self.t0 = time.time()
        self.ticks = 0

        while True:
            # DEBUGME: uncomment below
            # sys.stderr.write('yhxw = (%d/%d, %d/%d)\n' %
            #                  (self.y, self.garden_h, self.x, self.garden_w))
            if self.last_mower is not None:
                (ly, lx, ldir) = self.last_mower
                self.clear_mower(ly, lx, ldir)

            if self.state == 'mowing':
                dir = self.row_dir(self.y)
                # mow some grass
                if dir == 1:
                    self.mow_grass(self.y, self.x - (self.mower_size + 1))
                    self.right_mower(self.y, self.x)
                else:
                    self.mow_grass(self.y, self.x + self.mower_size + 1)
                    self.left_mower(self.y, self.x)
                self.last_mower = (self.y, self.x, dir)

                # update the position of the lawnmower
                self.x = self.x + dir

                if dir == 1 and self.x > self.garden_w + 2*self.mower_size + 1:
                    if self.y >= self.garden_h - 1:
                        self.return_side = 'right'
                        self.state = 'return_up'
                        self.x = self.garden_w + self.mower_size
                    else:
                        self.y = self.y + 1
                        self.x = self.garden_w + self.mower_size
                elif dir == -1 and self.x < 0:
                    if self.y >= self.garden_h - 1:
                        self.return_side = 'left'
                        self.state = 'return_up'
                        self.x = self.mower_size + 1
                    else:
                        self.y = self.y + 1
                        self.x = self.mower_size + 1

            elif self.state == 'return_up':
                dir = 1 if self.return_side == 'right' else -1
                if dir == 1:
                    self.right_mower(self.y, self.x)
                else:
                    self.left_mower(self.y, self.x)
                self.last_mower = (self.y, self.x, dir)

                if self.y <= 0:
                    self.cycle_start_side = self.return_side
                    if self.cycle_start_side == 'left':
                        self.y, self.x = (0, self.mower_size + 1)
                    else:
                        self.y, self.x = (0, self.garden_w + self.mower_size)
                    self.state = 'mowing'
                else:
                    self.y = self.y - 1

            # tick
            t = time.time()
            while (t - self.t0 < self.delay * self.ticks):
                curses.doupdate() # needed to trigger resize events
                self.handle_events()
                # sleep for at most .04s at a time so that resize is not too
                # laggy
                time.sleep(min(self.delay * self.ticks - (t - self.t0), .04))
                t = time.time()
            self.handle_events()
            now = time.time()
            self.regrow_grass(now)
            self.refresh_screen()
            self.ticks = self.ticks + 1
        
    def refresh_screen(self):
        (h, w) = self.term.getmaxyx()
        # warning: race condition! h & w might change before we call refresh.
        # this might cause curses to crash (?). oh, well...
        self.scr.refresh(0, self.mower_size + 1,
                         0, 0,
                         min(self.garden_h, h) - 1, min(self.garden_w, w) - 1)

def start(win):
    if catch_sigint:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    
    l = lawn(win,slow_delay,fast_delay,miss_permyriad)
    l.mow()

if __name__ == '__main__':
    curses.wrapper(start) # this performs curses initialisation & catches errors
