#!/usr/bin/env python

from __future__ import division

import curses
import logging
import math
import random
import time
import traceback

from ownercredit import pid, filtered, misc

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

    def offset( self, win, pos=None, off=None ):
        x,y			= pos or (0,0)
        if off:
            dx,dy		= off
            x,y			= x+dx,y+dy
        return x,y

    def clip( self, win, pos, throwing=True ):
        """Transform from system coordinates to screen coordinates and clip.  Assumes character cells are
        roughly twice as tall as they are wide.  Origin of screen coordinates is upper-left, but
        origin of system is lower-left.

        """
        rows,cols		= win.getmaxyx()
        x,y			= pos
        x,y			= x*2,rows-1-y
        if ( int( y ) < 0 or int( y ) >= rows or int( x ) < 0 or int( x ) >= cols ):
            if throwing:
                raise Clipped( "%r beyond range %r" % ( pos, (cols,rows) ))
            return None
        return x,y		# not clipped; transformed to screen coordinates

    def draw( self, win, pos=None, off=None, cleartoeol=False ):
        try:
            pos			= self.offset( win=win, pos=pos, off=off )
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
        return random.choice( super( exhaust, self ).thing )


class sprites( sprite ):
    """Support a list of [..., (off, thing), ...] """
    def draw( self, win, pos=None, off=None, **kwds ):
        if isinstance( self.thing, str ):
            # self.thing == 'a'
            super( sprites, self ).draw( win, pos=pos, off=off, **kwds )
        else:
            # An iterable of things, each w/ offset.  Get base position, not clipped
            pos			= self.offset( win=win, pos=pos, off=off )
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
    dv				= a(p) * dt

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
    def __init__( self, thing, position, velocity, acceleration, now=None ):
        self.position		= position
        self.velocity		= velocity
        self.acceleration	= acceleration
        super( body, self ).__init__( thing=thing )

    @property
    def done( self ):
        """If we're at/below ground level, we'll say we're done..."""
        return self.position[Y] <= 0

    def advance( self, dt, now=None ):
        """Compute new position, velocity from current acceleration, using the time quantum 'dt'"""
        pos,vel			= [],[]
        for p,v,a in zip( self.position, self.velocity, self.acceleration ):
            #p_new,v_new	= verlet( p, v, dt, lambda r: a)
            p_new,v_new		= homemade( p, v, dt, lambda r: a)
            pos.append( p_new )
            vel.append( v_new )
        self.position		= pos
        self.velocity		= vel

    def constrain( self ):
        """Constrain an object, if necessary.  By default, just crash and stop.  Returns a list of body
        instances to replace itself, if necessary, or None if no replacment"""
        if self.position[Y] <= 0 and self.velocity[Y] < 0:
            # We're at/below ground, and have -'ve vertical velocity.  Stop at current X.
            self.position[Y]	= 0
            self.velocity	= [0,0]
            self.acceleration	= [0,0]
        return None

    def update( self, win ):
        self.draw( win, self.position, cleartoeol=False )


class active( body ):
    """A body w/ mass, reactive to forces acting upon it.  Defaults to acceleration G in the Y axis."""
    def __init__( self, *args, **kwds ):
        """Capture any starting mass and thrust (default to 0 for each acceleration axis, if None)."""
        self.mass		= kwds.pop( 'mass' )		# required mass
        self.thrust		= kwds.pop( 'thrust', None )	# optional thrust
        self.limit		= None				# any thrust limits
        super( active, self ).__init__( *args, **kwds )
        if self.thrust is None:
            self.thrust		= [0 for _ in self.acceleration]

    def advance( self, dt, now=None ):
        """Thrust in kg.m/s^2 over mass in kg yields acceleration in m/s^2"""
        self.acceleration	= [ t/self.mass for t in self.thrust ]
        self.acceleration[Y]   += G
        super( active, self ).advance( dt, now=now )


class autopilot( object ):
    """Augments an active body (one with mass and thrust).  Attempts to achieve 0 velocity at the
    desired position, respecting any thrust limits.

    """
    def __init__( self, *args, **kwds ):
        self.target		= kwds.pop( 'target' )		# required target position
        Kpid			= kwds.pop( 'Kpid' )	 	# required PID loop tuning
        super( autopilot, self ).__init__( *args, **kwds )
        self.auto		= True
        self.controller		= pid.controller(
            Kpid	= Kpid, # If array, will be shared by all
            setpoint	= 0.,
            process	= filtered.weighted_linear( now=kwds.get('now'), interval=.25, value=0 ),#0.,#self.difference(),
            Lout	= (0,100.0),				# Output limit: 0-->100% thrust
            now		= kwds.get( 'now' ),			# Use simulation time base (if any)
        )

    def difference( self ):
        return self.position[Y] - self.target[Y]

    def advance( self, dt, now=None ):
        """Adjust thrust for the time period, based on position relative to target."""
        self.thrust[Y]	= self.limit * self.controller.loop(
            setpoint=0., process=self.difference(), now=now ) / 100
        return super( autopilot, self ).advance( dt, now=now )

    def update( self, win ):
        super( autopilot, self ).update( win )
        message( win, "%7.3f %% f: %9.3f N, a: %7.3f m/s^2, v: %7.3f m/s, P:%7.3f*%7.3f, I:%7.3f*%7.3f, D: %7.3f*%7.3f " % (
            self.controller.value, self.limit * self.controller.value / 100, self.acceleration[Y], self.velocity[Y],
            self.controller.P, self.controller.Kp,
            self.controller.I, self.controller.Ki,
            self.controller.D, self.controller.Kd ),
                 row=self.position[Y], col=self.position[X] + 3 )

class fragment( body, sprites ):
    """A body/sprite that draw a rotating fragment that modulates over time, 'til done (at which time it
    displays its native thing).  Disappears after some seconds."""
    def __init__( self, *args, **kwds ):
        self.speed		= random.randint( 1, 10 )
        self.timeout		= kwds.pop( 'timeout', None )
        super( fragment, self ).__init__( *args, **kwds )

    @sprites.thing.getter
    def thing( self ):
        if not self.done:
            return "|/-\\"[ int( timer() * 13 / self.speed ) % 4 ]
        return super( fragment, self ).thing

    def advance( self, dt, now=None ):
        if self.done:
            if self.timeout is not None:
                self.timeout	-= dt
        return super( fragment, self ).advance( dt, now=now )

    def constrain( self ):
        if self.timeout is not None and self.timeout <= 0:
            return []
        return super( fragment, self ).constrain()


class rocket( autopilot, active, sprites ):
    """An autopilot/active/sprites (eg. which draws a rocket w/ modulating flame), that converts itself into
    chunks of fragments on impact, and has thrust.

    """
    def __init__( self, *args, **kwds ):
        if 'target' not in kwds:
            kwds['target']	= [0,0]
        if not args and 'thing' not in kwds:
            kwds['thing']	= [
                (( 0, 1),'^'), 
                (( 0, 0), exhaust( "|!" )),
                (( 0,-1), sprites([
                    (( 0, 0), exhaust( "'`" )),
                ])),
                (( 0,-1), sprites([
                    (( 0, 0), exhaust( ";'`^!.," )),
                ])),
                (( 0,-1), sprites([
                    (( 0, 0), exhaust( "xo" )),
                    (( 0,-1), exhaust( ";'`^!.," )),
                ])),
                (( 0,-1), sprites([
                    (( 0, 0), exhaust( "XxOo" )),
                    (( 0,-1), exhaust( "xo" )),
                    (( 0,-2), exhaust( ";'`^!.," )),
                ])),
                (( 0,-1), sprites([
                    ((-.5,0), exhaust( "( " )),
                    (( 0, 0), exhaust( "XO" )),
                    ((.5, 0), exhaust( " )" )),
                    (( 0,-1), exhaust( "xo" )),
                    (( 0,-2), exhaust( ";'`^!.," )),
                ])),
                (( 0,-1), sprites([
                    ((-.5,0), exhaust( "(" )),
                    (( 0, 0), exhaust( "XO" )),
                    ((.5, 0), exhaust( ")" )),
                    (( 0,-1), exhaust( "xo" )),
                    (( 0,-2), exhaust( "xo" )),
                    (( 0,-3), exhaust( ";'`^!.," )),
                ])),
                (( 0,-1), sprites([
                    ((-.5,0), exhaust( "(" )),
                    (( 0, 0), exhaust( "XO" )),
                    ((.5, 0), exhaust( ")" )),
                    (( 0,-1), exhaust( "xo" )),
                    (( 0,-2), exhaust( "xo" )),
                    (( 0,-3), exhaust( "xo" )),
                    (( 0,-4), exhaust( ";'`^!.," )),
                ])),
            ]
            super( rocket, self ).__init__( *args, **kwds )
        # eg. 2 * 10m/s^2 * 1000kg == 20,000kg.m/s^2 max thrust
        self.limit		= 4 * -G * self.mass

    @sprites.thing.getter
    def thing( self ):
        thing			= super( rocket, self ).thing
        scale			= self.thrust[Y] / self.limit # range: [0.0,1.0]
        return thing[0:2+int( round(( len( thing ) - 2 ) * scale ))]

    def constrain( self ):
        if self.position[Y] <= 0 and self.velocity[Y] < -1:
            # Crash (> -1m/s velocity at touchdown).  Replace rocket w/ its chunks, roughly
            # splitting up its momentum...
            chunks		= []
            count		= random.randint( 2, 10 )
            momentum		= sum( v**2 for v in self.velocity ) ** .5 # magnitude of vector
            for chunk in range( count ):
                velocity	= [
                    random.uniform( -momentum/count*2, momentum/count*2 ),
                    random.uniform( -momentum/count*2, momentum/count*2 )
                ]	
                chunks.append( fragment( random.choice( 'xX!@#%^' ), timeout=5,
                    position=[self.position[X],0], acceleration=[0,G], velocity=velocity ))
            return chunks
        return super( rocket, self ).constrain()


def animation( win, title='Rocket', timewarp=1.0 ):
    last = now			= timer() # last/now starts off as real-time
    dt				= 0.0
    bodies			= []
    Kpid			= [ 5.0, 1.0, 10.0 ]
    while True:
        message( win, "Quit [q]? [W/w]arp:% 7.3f %7.3f FPS [PID/pid]: (%9.3f, %9.3f, %9.3f)" % (
            timewarp, 1.0/(dt/timewarp) if dt else float('inf'),
            Kpid[0], Kpid[1], Kpid[2] ),
                 cleartoeol=False )
        win.refresh()
        input                   = win.getch()

        if 0 <= input <= 255 and chr( input ) in ('q',):
            break

        # Timewarp
        if 0 <= input <= 255 and chr( input ) in "Ww":
            inc			= misc.magnitude( timewarp )
            timewarp	       += inc + inc/100 if chr( input ) in "W" else -inc + inc/100
            timewarp           -= timewarp % inc

        # PID
        if 0 <= input <= 255 and chr( input ) in "PpIiDd":
            index		= "PID".index( chr( input ).upper() )
            inc			= misc.magnitude( Kpid[index] )
            Kpid[index]	       += inc + inc/100 if chr( input ) in "PID" else -inc + inc/100
            Kpid[index]	       -= Kpid[index] % inc
            for b in bodies:
                if hasattr( b, 'controller' ):
                    b.controller.Kp,b.controller.Ki,b.controller.Kd \
                                = Kpid

        if 0 <= input <= 255 and chr( input ) in "x":
            for i,b in enumerate( bodies ):
                if hasattr( b, 'controller' ):
                    bodies.pop( i )
                    break
        if 0 <= input <= 255 and chr( input ) in "0123456789":
            for b in bodies:
                if hasattr( b, 'limit' ):
                    # has thrust limit; select 0-90% of limit thrust
                    b.thrust[Y]	= int( chr( input )) * b.limit / 10

        # Restart
        if 0 <= input <= 255 and chr( input ) in (' ',):
            bodies.append( rocket(
                now=now, Kpid=Kpid,
                mass=1000, target=[50,30],
                position=[50, 0], velocity=[0,0], acceleration=[0,G] ))

        # Next frame of animation
        win.erase()

        # Compute time advance, after time warp
        real                    = timer()
        dt                      = ( real - last ) * timewarp
        now		       += dt		# now advances by 'timewarp' (>/=/< real time)
        last                    = real

        bodies			= step( bodies, win, dt, now )
        for r,b in enumerate( b for b in bodies[::-1] if hasattr( b, 'limit' )):
            message( win, "%7.3f, %7.3f m/s: %7.3f kg.m/s^2" % (
                b.velocity[X], b.velocity[Y], b.thrust[Y] ), row=r+1, cleartoeol=False )
            

def step( bodies, win, dt, now ):
        bodies_new		= []
        for b in bodies:
            b.advance( dt, now )
            replacement		= b.constrain()
            bodies_new	       += [b] if replacement is None else replacement
        for b in bodies_new:
            b.update( win )
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
