from __future__ import division

import numpy as np
from scipy import optimize
import transforms3d

from ..optics.base import OpticalElement
from ..base import _parse_position_keywords, MarxsElement
from ..optics import FlatDetector
from ..math.rotations import ex2vec_fix
from ..math.pluecker import e2h, h2e
from ..simulator import Parallel


def find_radius_of_photon_shell(photons, mirror_shell, x, percentile=[1,99]):
    '''Find the radius the photons coming from a single mirror shell have.

    For nested Wolter Type I mirrors the ray of photons reflected from a single mirror
    shell essentially form a cone in space. The tip of the cone is at the focal point
    and the base is at the mirror. There is a certain thickness to this cone depending
    on where exactly on the mirror the individual photon was reflected.

    This function takes a photon list of photons after passing through the mirror and
    calculates the radius range that this photon cone covers at a specific distance from
    the focal point. This information can help to design the placement of gratings.

    Parameters
    ----------
    photons : `~astropy.table.Table`
        Photon list with position and direction of photons leaving the mirror
    mirror_shell : int
        Select mirror shell to look at (uses column ``mirror_shell`` in ``photons``
        for filtering).
    x : float
        Distance along the optical axis (assumed to coincide with the x axis with focal point
        at 0).
    percentile : list of floats
        The radius is calculated at the given percentiles. ``50`` would give the median radius.
        The default of ``[1, 99]`` gives a radius range excluding extrem outliers such as
        stray rays scattered into the extreme wing of the PSF.
    '''
    p = photons[:]
    mdet = FlatDetector(position=np.array([x, 0, 0]), zoom=1e8, pixsize=1.)
    p = mdet.process_photons(p)
    ind = (p['probability'] > 0) & (p['mirror_shell'] == mirror_shell)
    r = np.sqrt(p['det_x'][ind]**2+p['det_y'][ind]**2.)
    return np.percentile(r, percentile)


class RowlandTorus(MarxsElement):
    '''Torus with z axis as symmetry axis

    Parameters
    ----------
    R : float
        Radius of Rowland torus. ``r`` determines the radius of the Rowland circle,
        ``R`` is then used to rotate that circle around the axis of symmetry of the torus.
    r : float
        Radius of Rowland circle
    '''
    def __init__(self, R, r, **kwargs):
        self.R = R
        self.r = r
        self.pos4d = _parse_position_keywords(kwargs)
        super(RowlandTorus, self).__init__(**kwargs)

    def quartic(self, xyz, transform=True):
        '''Quartic torus equation.

        Roots of this equation are points on the torus.

        Parameters
        ----------
        xyz : np.array of shape (N, 3) or (3)
            Coordinates of points in euklidean space. The quartic is calculated for
            those points.
        transform : bool
            If ``True`` transform ``xyz`` from the global coordinate system into the
            local coordinate system of the torus. If this transformation is done in the
            calling function already, set to ``False``.

        Returns
        -------
        q : np.array of shape (N) or scalar
            Quartic at the input location
        '''
        if xyz.shape[-1] != 3:
            raise ValueError('Input coordinates must be defined in Eukledian space.')

        if transform:
            invpos4d = np.linalg.inv(self.pos4d)
            xyz = h2e(np.einsum('...ij,...j', invpos4d, e2h(xyz, 1)))
        return ((xyz**2).sum(axis=-1) + self.R**2. - self.r**2.)**2. - 4. * self.R**2. * (xyz[..., :2]**2).sum(axis=-1)

    def solve_quartic(self, x=None, y=None, z=None, interval=[0, 1]):
        '''Solve the quartic for points on the Rowland torus in Cartesian coordinates.

        This method solves the quartic equation for positions on the Rowland Torus for
        cases where two of the Cartesian coordinates are fixed (e.g. y and z) and the third
        one (e.g. x) needs to be computed. This function is intended as a convenience for a
        common use case. In more general cases, evaluate the :meth:`RowlandTorus.quartic` and
        search for the roots of that function.

        Parameters
        ----------
        x, y, z : float or None
            Set two of these coordinates to fixed numbers. This method will solve for the
            coordinate set to ``None``.
            x, y, z are defined in the global coordinate system.
        interval : np.array
            [min, max] for the search. The quartic can have up to for solutions because a
            line can intersect a torus in four points and this interval must bracket one and only
            one solution.

        Returns
        -------
        coo : float
            Value of the fitted coordinate.
        '''
        n_Nones = 0
        for i, c in enumerate([x, y, z]):
            if c is None:
                n_Nones +=1
                ind = i
        if n_Nones != 1:
            raise ValueError('Exactly one of the input numbers for x,y,z must be None.')
        # Need to give it a number for vstack to work
        if ind == 0: x = 0.
        if ind == 1: y = 0.
        if ind == 2: z = 0.

        xyz = np.vstack([x,y,z]).T
        def f(val_in):
            xyz[..., ind] = val_in
            return self.quartic(xyz)
        val_out, brent_out = optimize.brentq(f, interval[0], interval[1], full_output=True)
        if not brent_out.converged:
            raise Exception('Intersection with torus not found.')
        return val_out


    def normal(self, xyz):
        '''Return the gradient vector field.

        Parameters
        ----------
        xyz : np.array of shape (N, 3) or (3)
            Coordinates of points in euklidean space. The quartic is calculated for
            those points. All points need to be on the surface of the torus.

        Returns
        -------
        gradient : np.array
            Gradient vector field in euklidean coordinates. One vector corresponds to each
            input point. The shape of ``gradient`` is the same as the shape of ``xyz``.
        '''
        # For r,R  >> 1 even marginal differences lead to large
        # numbers on the quartic because of R**4 -> normalize
        invpos4d = np.linalg.inv(self.pos4d)
        xyz = h2e(np.einsum('...ij,...j', invpos4d, e2h(xyz, 1)))

        if not np.allclose(self.quartic(xyz, transform=False) / self.R**4., 0.):
            raise ValueError('Gradient vector field is only defined for points on torus surface.')
        factor = 4. * ((xyz**2).sum(axis=-1) + self.R**2. - self.r**2)
        dFdx = factor * xyz[..., 0] - 8. * self.R**2 * xyz[..., 0]
        dFdy = factor * xyz[..., 1] - 8. * self.R**2 * xyz[..., 1]
        dFdz = factor * xyz[..., 2]
        gradient = np.vstack([dFdx, dFdy, dFdz]).T
        return h2e(np.einsum('...ij,...j', self.pos4d, e2h(gradient, 0)))

def design_tilted_torus(f, alpha, beta):
    '''Design a torus with specifications similar to Heilmann et al. 2010

    A `RowlandTorus` is fully specified with the parameters ``r``, ``R`` and ``pos4d``.
    However, in practice, these numbers might be derived from other values.
    This function calculates the parameters of a RowlandTorus, based on a different
    set of input values.

    Parameters
    ----------
    f : float
        distance between focal point and on-axis grating. Should be as large as
        possible given the limitations of the spacecraft to increase the resolution.
    alpha : float (in radian)
        angle between optical axis and the line (on-axis grating - center of Rowland circle).
        A typical value could be twice the blaze angle.
    beta : float (in radian)
        angle between optical axis and the line (on-axis grating - hinge), where the hinge
        is a point on the Rowland circle. The Rowland Torus will be constructed by rotating
        the Rowland Circle around the axis (focal point - hinge).
        The center of the Rowland Torus will be the point where the line
        (on-axis grating - center of Rowland circle) intersects the line
        (focal point - hinge).

    Returns
    -------
    R : float
        Radius of Rowland torus. ``r`` determines the radius of the Rowland circle,
        ``R`` is then used to rotate that circle around the axis of symmetry of the torus.
    r : float
        Radius of Rowland circle
    pos4d : np.array of shape (4, 4)

    Notes
    -----
    The geometry used here really needs to be explained in a figure.
    However, some notes to explain at least the meaning of the symbols on the code
    are in order:

    - Cat : position of on-axis CAT grating (where the Rowland circle intersects the on-axis beam)
    - H : position of hinge
    - Ct : Center of Rowland Torus
    - F : Focal point on axis (at the origin of the coordinate system)
    - CatH, HF, FCt, etc. : distance between Cat and H, F and Ct, etc.
    - gamma : see sketch.
    '''
    r = f / (2. * np.cos(alpha))
    CatH = r * np.sqrt(2 * (1 + np.cos(2 * (beta - alpha))))
    HF = np.sqrt(f**2 + CatH**2 - 2 * f * CatH * np.cos(beta))
    # If alpha is negative, then everything is "on the other side".
    # The sign of gamma cannot be found through the arccos, so need to set it here
    # with sign(alpha).
    # Another gotcha: np.sign(0) = 0, but we want 1 (or -1)
    gamma = np.arccos(HF / (2 * r)) * (np.sign(alpha) or 1)
    R = f / np.sin(np.pi - alpha - (alpha + gamma)) * np.sin(alpha + gamma) - r
    FCt = f / np.sin(np.pi - alpha - (alpha + gamma)) * np.sin(alpha)
    x_Ct = FCt * np.cos(alpha + gamma)
    y_Ct = 0
    z_Ct = FCt * np.sin(alpha + gamma)
    orientation = transforms3d.axangles.axangle2mat([0,1,0], np.pi/2 - alpha - gamma)
    pos4d = transforms3d.affines.compose([x_Ct, y_Ct, z_Ct], orientation, np.ones(3))
    return R, r, pos4d

class FacetPlacementError(Exception):
    pass


class GratingArrayStructure(Parallel, OpticalElement):
    '''A collection of diffraction gratings on the Rowland torus.

    When a ``GratingArrayStructure`` (GAS) is initialized, it places
    grating facets in the space available on the Rowland circle.

    After generation, individual facet positions can be adjusted by hand by
    editing the attributes `elem_pos` or `elem_uncertainty`. See `Parallel` for details.

    After any of the :attribute:`elem_pos`, :attribute:`elem_uncertainty` or
    :attribute:`uncertainty` is changed, :method:`generate_elements` needs to be
    called to regenerate the facets on the GAS.

    Parameters
    ----------
    rowland : RowlandTorus
    d_facet : float
        Size of the edge of a facet, which is assumed to be flat and square.
        (``d_facet`` can be larger than the actual size of the silicon membrane to
        accommodate a minimum thickness of the surrounding frame.)
    x_range: list of 2 floats
        Minimum and maximum of the x coordinate that is searched for an intersection
        with the torus. A ray can intersect a torus in up to four points. ``x_range``
        specififes the range for the numerical search for the intersection point.
    radius : list of 2 floats
        Inner and outer radius of the GAS as measured in the yz-plane from the
        origin.
    phi : list of 2 floats
        Bounding angles for a segment covered by the GSA. :math:`\phi=0`
        is on the positive y axis. The segment fills the space from ``phi1`` to
        ``phi2`` in the usual mathematical way (counterclockwise).
        Angles are given in radian. Note that ``phi[1] < phi[0]`` is possible if
        the segment crosses the y axis.
    '''

    tangent_to_torus = False
    '''If ``True`` the default orientation (before applying blaze, uncertainties etc.) of facets is
    such that they are tangents to the torus in the center of the facet.
    If ``False`` they are perpendicular to perfectly focussed rays.
    '''

    id_col = 'facet'

    def __init__(self, rowland, d_facet, x_range, radius, phi=[0., 2*np.pi], **kwargs):
        self.rowland = rowland
        if not (radius[1] > radius[0]):
            raise ValueError('Outer radius must be larger than inner radius.')
        if np.min(radius) < 0:
            raise ValueError('Radius must be positive.')
        self.radius = radius

        if np.max(np.abs(phi)) > 10:
            raise ValueError('Input angles >> 2 pi. Did you use degrees (radian expected)?')
        self.phi = phi
        self.x_range = x_range
        self.d_facet = d_facet

        super(GratingArrayStructure, self).__init__(**kwargs)

    def calc_ideal_center(self):
        '''Position of the center of the GSA, assuming placement on the Rowland circle.'''
        anglediff = (self.phi[1] - self.phi[0]) % (2. * np.pi)
        a = (self.phi[0] + anglediff / 2 ) % (2. * np.pi)
        r = sum(self.radius) / 2
        return self.xyz_from_ra(r, a).flatten()

    def anglediff(self):
        '''Angles range covered by facets, accounting for 2 pi properly'''
        anglediff = (self.phi[1] - self.phi[0])
        if (anglediff < 0.) or (anglediff > (2. * np.pi)):
            # If anglediff == 2 pi exactly, presumably the user want to cover the full circle.
            anglediff = anglediff % (2. * np.pi)
        return anglediff

    def max_facets_on_arc(self, radius):
        '''Calculate maximal number of facets that can be placed at a certain radius.

        Parameters
        ----------
        radius : float
            Radius of circle where the centers of all facets will be placed.
        '''
        return radius * self.anglediff() // self.d_facet

    def distribute_facets_on_arc(self, radius):
        '''Distribute facets on an arc.

        The facets are distributed as evenly as possible over the arc.

        ..note::

          Contrary to `distribute_facets_on_radius`, facets never stretch beyond the limits set by the ``phi`` parameter
          of the GAS. If an arc segment is not wide enough to accommodate at least a single facet,
          it will go empty.

        Parameters
        ----------
        radius : float
            radius of arc where the facets are to be distributed.

        Returns
        -------
        centerangles : array
            The phi angles for centers of the facets at ``radius``.
        '''
        # arc is most crowded on inner radius
        n = self.max_facets_on_arc(radius - self.d_facet / 2)
        facet_angle = self.d_facet / (2. * np.pi * radius)
        # thickness of space between facets, distributed equally
        d_between = (self.anglediff() - n * facet_angle) / (n + 1)
        centerangles = d_between + 0.5 * facet_angle + np.arange(n) * (d_between + facet_angle)
        return (self.phi[0] + centerangles) % (2. * np.pi)

    def max_facets_on_radius(self):
        '''Distribute facets on a radius.

        Returns
        -------
        n : int
            Number of facets needed to cover a given radius segment.
            Facets might reach beyond the radius limits if the difference between
            inner and outer radius is not an integer multiple of the facet size.
        '''
        return int(np.ceil((self.radius[1] - self.radius[0]) / self.d_facet))

    def distribute_facets_on_radius(self):
        '''Distributes facets as evenly as possible along a radius.

        .. note::
           Unlike `distribute_facets_on_arc`, this function will have facets reaching
           beyond the limits of the radius, if the distance between inner and outer radius
           is not an integer multiple of the facet size.

        Returns
        -------
        radii : np.ndarray
            Radii of the facet *center* positions.
        '''
        n = self.max_facets_on_radius()
        return np.mean(self.radius) + np.arange(- n / 2 + 0.5, n / 2 + 0.5) * self.d_facet

    def xyz_from_ra(self, radius, angle):
        '''Get Cartesian coordiantes for radius, angle and the rowland circle.

        y,z are calculated from the radius and angle of polar coordiantes in a plane;
        then x is determined from the condition that the point lies on the Rowland circle.
        '''
        y = radius * np.sin(angle)
        z = radius * np.cos(angle)
        x = self.rowland.solve_quartic(y=y,z=z, interval=self.x_range)
        return np.vstack([x,y,z]).T

    def calculate_elempos(self):
        '''Calculate ideal facet positions based on rowland geometry.

        Returns
        -------
        pos4d : list of arrays
            List of affine transformations that bring an optical element centered
            on the origin of the coordinate system with the active plane in the
            yz-plane to the required facet position on the Rowland torus.
        '''
        pos4d = []
        radii = self.distribute_facets_on_radius()
        for r in radii:
            angles = self.distribute_facets_on_arc(r)
            for a in angles:
                facet_pos = self.xyz_from_ra(r, a).flatten()
                if self.tangent_to_torus:
                    facet_normal = np.array(self.rowland.normal(facet_pos))
                else:
                    facet_normal = facet_pos
                # Find the rotation between [1, 0, 0] and the new normal
                # Keep grooves (along e_y) parallel to e_y
                rot_mat = ex2vec_fix(facet_normal, np.array([0., 1., 0.]))

                pos4d.append(transforms3d.affines.compose(facet_pos, rot_mat, np.ones(3)))
        return pos4d
