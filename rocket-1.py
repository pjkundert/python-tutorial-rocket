#!/usr/bin/env python

import curses
import logging
import math
import random
import time
import traceback

timer				= time.time	# A sub-second (preferably sub-millisecond) timer

class Clipped( Exception ):
    pass


class sprite( object ):
    """Base sprite class; The thing must be a str.  System coordinates are 0,0 in lower left corner;
    Screen coordinates are 0,0 in upper left corner.  All computations are in system coordinates,
    until the moment of drawing.
    
    """
    def __init__( self, thing ):
        """Remember thing to draw.  This base class only supports a str"""
        self._thing		= None
        self.thing		= thing

    @property
    def thing( self ):
        return self._thing
    @thing.setter
    def thing( self, value ):
        self._thing = value

    @property
    def done( self ):
        return False

    def transform( self, win, pos=None, off=None ):
        x,y			= pos or (0,0)
        if off:
            dx,dy		= off
            x,y			= x+dx,y+dy
        return x,y

    def clip( self, win, pos, throwing=True ):
        """Clip system coordinates, transform to screen coordinates and clip."""
        rows,cols		= win.getmaxyx()
        x,y			= pos
        if ( int( y ) < 0 or int( y ) >= rows or int( x ) >= cols ):
            if throwing:
                raise Clipped( "%r beyond range %r" % ( pos, (cols,rows) ))
            return None
        return x,rows-1-y # not clipped; transform to screen coordinates

    def draw( self, win, pos=None, off=None, cleartoeol=False ):
        try:
            pos			= self.transform( win=win, pos=pos, off=off )
            x,y			= self.clip( win=win, pos=pos )
            if cleartoeol:
                win.move( int( y ), int( x ))
                win.clrtoeol()
            win.addstr( int( y ), int( x ), self.thing )
        except Clipped:
            pass


class exhaust( sprite ):
    """A sprite that draw a flame-like symbol that modulates over time."""
    @sprite.thing.getter
    def thing( self ):
        thing			= super( exhaust, self ).thing
        return thing[ int( timer() * 97 ) % len( thing ) ] # eg. ";'`^!.,"



class sprites( sprite ):
    """Support a list of [..., (off, thing), ...] """
    def draw( self, win, pos=None, off=None, **kwds ):
        if isinstance( self.thing, str ):
            # self.thing == 'a'
            super( sprites, self ).draw( win, pos=pos, off=off, **kwds )
        else:
            # An iterable of things, each w/ offset.  Get base position, not clipped
            pos			= self.transform( win=win, pos=pos, off=off )
            for off,spr in self.thing:
                # [..., ( off, <sprite> ), ... ]
                if isinstance( spr, sprite ):
                    spr.draw( win, pos=pos, off=off, **kwds )
                    continue
                # [..., ( off, 'a' ), ...]  Create a <sprite> instance to draw
                assert isinstance( spr, str )
                sprite( spr ).draw( win=win, pos=pos, off=off, **kwds )


def message( win, text, row=None, col=None, cleartoeol=True ):
    """Default location for message is bottom row"""
    sprite( text ).draw( win=win, pos=(col or 0,row or 0), cleartoeol=cleartoeol )


def verlet(p, v, dt, a):
    """Return new position and velocity from current values, time step and acceleration.

    Parameters:
    p is a numpy array giving the current position vector
    v is a numpy array giving the current velocity vector
    dt is a float value giving the length of the integration time step
    a is a function which takes x as a parameter and returns the acceleration vector as an array

    Works with numpy arrays of any dimension as long as they're all the same, or with scalars.
    """
    # Deceptively simple (read about Velocity Verlet on wikipedia)
    p_new = p + v*dt + a(p)*dt**2/2
    v_new = v + (a(p) + a(p_new))/2 * dt
    return (p_new, v_new)


G				= -9.81 # m/s^2 Gravity, Earth surface avg.

def net_thrust( thrust, mass ):
    """Simple vertical thrust, net of gravity.  Ignores gravity changes w/ altitude..."""
    return G + thrust / mass


def homemade( p, v, dt, a ):
    """Return new position and velocity from current values, time step and acceleration.

    Parameters:
    p is a numpy array giving the current position vector
    v is a numpy array giving the current velocity vector
    dt is a float value giving the length of the integration time step
    a is a function which takes x as a parameter and returns the acceleration vector as an array
    """
    # Compute current altitude 'y', based on elapsed time 'dt' Compute acceleration f = ma,
    # a=f/m, including g.
    dv				= a * dt

    # Compute ending velocity v_new = v + at
    v_new			= v + dv

    # Compute ending position from avg. velocity over period dt
    v_ave			= ( v + v_new ) / 2.
    dp				= v_ave * dt
    p_new			= p + dp

    # and compute actual displacement and hence actual net acceleration for period dt
    #v_ave_act			= ( p_new - p ) / dt

    # we have an average velocity over the time period; we can deduce ending velocity, and
    # from that, the actual net acceleration experienced over the period by a = ( v - v0 ) / t
    #v_act			= ( v_ave_act - v ) * 2.
    #a_act			= ( v_act - v ) / dt

    return (p_new, v_new)

X				= 0
Y				= 1

class body( object ):
    """A physical body, w/ initial position/velocity/acceleration in N dimensions.  Combine with a
    sprite object, to give it physical position, velocity and acceleration capabilities:

    class something( body, sprites ):
        pass

    """
    def __init__( self, thing, position, velocity, acceleration ):
        self.position		= position
        self.velocity		= velocity
        self.acceleration	= acceleration
        super( body, self ).__init__( thing )

    @property
    def done( self ):
        """If we're at/below ground level, we'll say we're done..."""
        return self.position[Y] <= 0

    def advance( self, dt ):
        """Compute new position, velocity"""
        pos,vel			= [],[]
        for p,v,a in zip( self.position, self.velocity, self.acceleration ):
            p_new,v_new		= verlet( p, v, dt, lambda r: a)
            pos.append( p_new )
            vel.append( v_new )
        self.position		= pos
        self.velocity		= vel

    def constrain( self ):
        """Constrain an object, if necessary.  By default, just crash and stop.  Returns a list of body
        instances to replace itself, if necessary, or None if no replacment"""
        if self.position[Y] <= 0 and self.acceleration[Y] < 0:
            # We're at/below ground, and have -'ve vertical acceleration.  Stop at current X.
            self.position[Y]	= 0
            self.velocity	= [0,0]
            self.acceleration	= [0,0]
        return None

    def update( self, win ):
        self.draw( win, self.position, cleartoeol=False )


class fragment( body, sprites ):
    """A body/sprite that draw a rotating fragment that modulates over time, 'til done (at which time it
    displays its native thing)."""
    def __init__( self, *args, **kwds ):
        self.speed		= random.randint( 1, 10 )
        self.offset		= random.randint( 0, 3 )
        super( fragment, self ).__init__( *args, **kwds )

    @sprites.thing.getter
    def thing( self ):
        if not self.done:
            return "|/-\\"[ int( self.offset + timer() * 13 / self.speed ) % 4 ]
        return super( fragment, self ).thing


class rocket( body, sprites ):
    """A body/sprites (eg. which draws a rocket w/ modulating flame), that converts itself into chunks
    of fragments on impact.

    """
    def constrain( self ):
        if self.position[Y] <= 0 and self.velocity[Y] < -1:
            # Crash (> -1m/s velocity at touchdown).  Replace rocket w/ its chunks...
            chunks		= []
            for chunk in range( 10 ):
                chunks.append( fragment(
                    'x', position=[self.position[X],0], acceleration=[0,G], velocity=[chunk-5, 20] ))
            return chunks
        return super( rocket, self ).constrain()


def animation( win, title='Rocket', timewarp=1.0 ):
    last = now			= timer()
    dt				= 0.0
    bodies			= []

    while True:
        message( win, "Quit [q]? Warp:% 7.3f [W/w] %7.3f FPS" % (
                timewarp, 1.0/(dt/timewarp) if dt else float('inf')), cleartoeol=False )
        win.refresh()
        input                   = win.getch()

        if 0 <= input <= 255 and chr( input ) in ('q',):
            break

        # Timewarp
        if 0 <= input <= 255 and chr( input ) == 'W':
            timewarp           /= .95
        if 0 <= input <= 255 and chr( input ) == 'w':
            timewarp           *= .95

        # Restart
        if 0 <= input <= 255 and chr( input ) in (' ',):
            bodies.append( rocket(
                [ ((0,1),'^'), ((0,0),'|'), ([0,-1],exhaust( ";'`^!.," )) ],
                position=[50, 0], velocity=[0,30], acceleration=[0,G] ))

        # Next frame of animation
        win.erase()

        # Compute time advance, after time warp
        real                    = timer()
        dt                      = ( real - last ) * timewarp
        last                    = real

        now                    += dt

        bodies			= step( bodies, dt )
        for b in bodies:
            b.update( win )

        if bodies:
            message( win, "%7.3fms^2" % bodies[-1].velocity[Y], row=1, cleartoeol=False )


def step( bodies, dt ):
        bodies_new		= []
        for b in bodies:
            b.advance( dt )
            replacement		= b.constrain()
            bodies_new	       += [b] if replacement is None else replacement
        return bodies_new


def main( animate ):
    failure			= None
    try:
        # Initialize curses
        stdscr=curses.initscr()
        curses.noecho();
        curses.cbreak();
        curses.halfdelay( 1 )
        stdscr.keypad( 1 )

        animate( stdscr )

    except:
        failure			= traceback.format_exc()
    finally:
        # Terminate curses
        stdscr.keypad( 0 )
        curses.echo();
        curses.nocbreak()
        curses.endwin()
        time.sleep( .1 )

    if failure:
        logging.error( "Curses GUI Exception: %s", failure )


if __name__=='__main__':

    logging_cfg			= {
        "level":	logging.WARNING,
       #"level":	logging.INFO,
       #"level":	logging.DEBUG,
        "datefmt":	'%m-%d %H:%M:%S',
        "format":	'%(asctime)s.%(msecs).03d %(threadName)-10.10s %(name)-8.8s %(levelname)-8.8s %(funcName)-16.16s %(message)s',
       #"filename":	"log/jespersen.log",
    }

    logging.basicConfig( **logging_cfg )

    main( animate=animation )
