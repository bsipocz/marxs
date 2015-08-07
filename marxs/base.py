from collections import OrderedDict

import numpy as np
from transforms3d import affines

from astropy.table import Column

class MarxsElement(object):
    '''Base class for all elements in a MARXS simulation.

    This includes elements that actually change photon properties such as grating and
    mirrors, but also abstract concepts that do not have a direct hardware
    representation such as a "Rowland Torus".
    '''
    def __init__(self, **kwargs):
        if 'name' in kwargs:
            self.name = kwargs.pop('name')
        else:
            self.name = self.__class__

        if len(kwargs) > 0:
            raise ValueError('Initialization arguments {0} not understood'.format(', '.join(kwargs.keys())))

    def describe(self):
        return OrderedDict(element=self.name)

class SimulationSequenceElement(MarxsElement):
    '''Base class for all elements in a simulation sequence that process photons.'''
    output_columns = []
    '''This is a list of strings that names the output properties.

    This gives the names of the output properties from this optical element.
    `process_photon` or `process_photons` are responsible for calculating the values of these
    properties. For example, for a mirror of nested shells one might set
    ``output_columns = ['mirror_shell']`` to pass the information on which shell the interaction
    took place to the user.

    The following properties are always included in the output and do not need to be listed here:

        dir : `numpy.ndarray`
            4-d direction vector of ray in homogeneous coordinates
        pos : `numpy.ndarray`
            4-d position of last interaction pf the photons with any optical element in
            homogeneous coordinates. Together with ``dir`` this determines the equation
            of the ray.
        energy : float
            Photon energy in keV.
        polarization : float
            Polarization angle of the photons.
        probability : float
            Probability that the photon continues. Set to 0 if the photon is absorbed, to 1 if it
            passes the optical element and to number between 0 and 1 to express a probability that
            the photons passes.
    '''

    def add_output_cols(self, photons):
        '''Add output columns of the correct format (currently: float) to the photon array.

        The objects `output_columns` attribute lists the names of all columns that will be added.
        '''
        temp = np.empty(len(photons))
        temp[:] = np.nan
        for n in self.output_columns:
            if n not in photons.colnames:
                photons.add_column(Column(name=n, data=temp))


    def __call__(self, photons, *args, **kwargs):
        return self.process_photons(photons, *args, **kwargs)


def _parse_position_keywords(kwargs):
    '''Parse keywords to define position.

    If ``pos4d`` is given, use that, otherwise look for ``position``, ``zoom``
    and ``orientation``.

    Parameters
    ----------
    pos4d : 4x4 array
        Transformation to bring an element from the default position (see description of
        individual elements) to a certain point in the space of homogeneous coordinates.
    position : 3-d vector in real space
        Measured from the origin of the spacecraft coordinate system.
    orientation : Rotation matrix or ``None``
        Relative orientation of the base vectors of this optical
        element relative to the orientation of the coordinate system
        of the spacecraft. The default is no rotation (i.e. the axes of both
        coordinate systems are parallel).
    zoom : float or 3-d vector
        Scale the size of an optical element in each dimension by ``zoom``. If ``zoom`` is a scalar,
        the same scale is applied in all dimesions. This only affects the oter dimensions of
        the optical element, not internal scales like the pixel size or grating constant (if
        defined for the optical element in question).
    '''
    pos4d = kwargs.pop('pos4d', None)
    if pos4d is None:
        position = kwargs.pop('position', np.zeros(3))
        orientation = kwargs.pop('orientation', np.eye(3))
        zoom = kwargs.pop('zoom', 1.)
        if np.isscalar(zoom):
            zoom = np.ones(3) * zoom
        if not len(zoom) == 3:
            raise ValueError('zoom must have three elements for x,y,z or be a scalar (global zoom).')
        pos4d = affines.compose(position, orientation, zoom)
    else:
        if ('position' in kwargs) or ('orientation' in kwargs) or ('zoom' in kwargs):
            raise ValueError('If pos4d is specificed, the following keywords cannot be given at the same time: position, orientation, zoom.')
    return pos4d
