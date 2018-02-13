from __future__ import print_function
import logging
import cPickle as pickle
from dials.array_family import flex
import numpy as np
from math import pi
import copy
import logging
from cctbx import miller, crystal

logger = logging.getLogger('dials')

def save_experiments(experiments, filename):
  ''' Save the profile model parameters. '''
  from time import time
  from dxtbx.model.experiment_list import ExperimentListDumper
  st = time()
  logger.info('\nSaving the experiments to %s' % filename)
  dump = ExperimentListDumper(experiments)
  with open(filename, "w") as outfile:
    outfile.write(dump.as_json(split=True))
  logger.info('Time taken: %g' % (time() - st))

def parse_multiple_datasets(reflections):
  'method to parse multiple datasets from single reflection tables, selecting on id'
  single_reflection_tables = []
  for refl_table in reflections:
    dataset_ids = set(refl_table['id']).difference(set([-1]))
    print(dataset_ids)
    n_datasets = len(dataset_ids)
    if n_datasets > 1:
      logger.info(('\nDetected existence of a multi-dataset scaled reflection table {sep}'
        'containing {0} datasets. {sep}').format(n_datasets, sep='\n'))
      for dataset_id in dataset_ids:
        single_refl_table = refl_table.select(refl_table['id'] == dataset_id)
        single_reflection_tables.append(single_refl_table)
    else:
      single_reflection_tables.append(refl_table)
  logger.info("Found %s reflection tables in total." % len(single_reflection_tables))
  return single_reflection_tables


def calc_crystal_frame_vectors(reflection_table, experiments):
  '''calculates diffraction vector in crystal frame'''
  reflection_table['s0'] = flex.vec3_double([experiments.beam.get_s0()]*len(reflection_table))
  rot_axis = experiments.goniometer.get_rotation_axis()
  angles = reflection_table['phi'] * -1.0 * pi / 180 #want to do an inverse rot.
  reflection_table['s1c'] = rotate_vectors_about_axis(rot_axis,
    reflection_table['s1'], angles)
  reflection_table['s0c'] = rotate_vectors_about_axis(rot_axis,
    reflection_table['s0'], angles) #all s2 vectors now relative to a 'fixed crystal'
  reflection_table['s1c'] = align_rotation_axis_along_z(rot_axis,
    reflection_table['s1c'])
  reflection_table['s0c'] = align_rotation_axis_along_z(rot_axis,
    reflection_table['s0c'])
  return reflection_table

def rotate_vectors_about_axis(rot_axis, vectors, angles):
  #assumes angles in radians
  (r0, r1, r2) = vectors.parts()
  (ux, uy, uz) = list(rot_axis)
  #normalise
  modulus = (ux**2 + uy**2 + uz**2)**0.5
  (ux, uy, uz) = (ux/modulus, uy/modulus, uz/modulus)
  c_ph = flex.double(np.cos(angles))
  s_ph = flex.double(np.sin(angles))
  rx = (((c_ph + ((ux**2) * (1.0 - c_ph))) * r0)
        + (((ux * uy * (1.0 - c_ph)) - (uz * s_ph)) * r1)
        + (((uz * ux * (1.0 - c_ph)) + (uy * s_ph)) * r2))
  ry = ((((ux * uy * (1.0 - c_ph)) + (uz * s_ph)) * r0)
        + ((c_ph + ((uy**2) * (1.0 - c_ph))) * r1)
        + (((uz * uy * (1.0 - c_ph)) - (ux * s_ph)) * r2))
  rz = ((((ux * uz * (1.0 - c_ph)) - (uy * s_ph)) * r0)
        + (((uy * uz * (1.0 - c_ph)) + (ux * s_ph)) * r1)
        + ((c_ph + ((uz**2) * (1.0 - c_ph))) * r2))
  rotated_vectors = zip(rx, ry, rz)
  return flex.vec3_double(rotated_vectors)

def align_rotation_axis_along_z(exp_rot_axis, vectors):
  (ux, uy, uz) = list(exp_rot_axis)
  cross_prod_uz = (uy, -1.0*ux, 0.0)
  #cpx, cpy, cpz = list(cross_prod_uz)
  from math import acos
  angle_between_u_z = -1.0*acos(uz/((ux**2 + uy**2 + uz**2)**0.5))
  phi = flex.double([angle_between_u_z]*len(vectors))
  new_vectors = rotate_vectors_about_axis(cross_prod_uz, vectors, phi)
  return flex.vec3_double(new_vectors)

def sph_harm_table(reflection_table, experiments, lmax):
  from scitbx import math, sparse
  import math as pymath
  reflection_table = calc_crystal_frame_vectors(reflection_table, experiments)
  order = lmax
  lfg = math.log_factorial_generator(2 * order + 1)
  n_params = 0
  for i in range(1, lmax+1):
    n_params += (2*i) +1
  sph_harm_terms = sparse.matrix(len(reflection_table), n_params)
  (x1, y1, z1) = reflection_table['s0c'].parts()
  (x2, y2, z2) = reflection_table['s1c'].parts()

  phi_list = flex.double(np.arctan2(y1, x1))
  theta_list = flex.double(np.arctan2((((x1**2) + (y1**2))**0.5), z1))
  phi_list_2 = flex.double(np.arctan2(y2, x2))
  theta_list_2 = flex.double(np.arctan2((((x2**2) + (y2**2))**0.5), z2))
  sqrt2 = pymath.sqrt(2)
  nsssphe = math.nss_spherical_harmonics(order, 50000, lfg)
  counter = 0
  ziplist = zip(phi_list, theta_list, phi_list_2, theta_list_2)

  for l in range(1, lmax+1):
    for m in range(-l, l+1):
      if m < 0:
        for i, (phi, theta, phi2, theta2) in enumerate(ziplist):
          sph_harm_terms[i, counter] = (sqrt2 * ((-1) ** m)
            * (nsssphe.spherical_harmonic(l, -1*m, theta, phi).imag
            + nsssphe.spherical_harmonic(l, -1*m, theta2, phi2).imag)/2.0)
      elif m == 0:
        for i, (phi, theta, phi2, theta2) in enumerate(ziplist):
          sph_harm_terms[i, counter] = ((
            nsssphe.spherical_harmonic(l, m, theta, phi).real
            + nsssphe.spherical_harmonic(l, m, theta2, phi2).real)/2.0)
      else:
        for i, (phi, theta, phi2, theta2) in enumerate(ziplist):
          sph_harm_terms[i, counter] = (sqrt2 * ((-1) ** m)
            * (nsssphe.spherical_harmonic(l, m, theta, phi).real
            + nsssphe.spherical_harmonic(l, m, theta2, phi2).real)/2.0)
      counter += 1
  return sph_harm_terms

def reject_outliers(dm, zmax):
  '''simple, quick, outlier rejection based on normalised deviations
  (similar to aimless)'''
  sel = flex.bool([True]*len(dm.reflection_table))
  z_score = flex.double([])
  for i, _ in enumerate(dm.Ih_table.h_index_counter_array):
    h_idx_cumul = dm.Ih_table.h_index_cumulative_array[i:i+2]
    I = dm.Ih_table.intensities[h_idx_cumul[0]:h_idx_cumul[1]]
    g = dm.Ih_table.inverse_scale_factors[h_idx_cumul[0]:h_idx_cumul[1]]
    w = dm.Ih_table.weights[h_idx_cumul[0]:h_idx_cumul[1]]
    wgIsum = flex.sum(I*g*w)
    wg2sum = flex.sum(w*g*g)
    norm_dev_list = (I - (g * wgIsum/wg2sum))/(((1.0/w)+((g/wg2sum)**2))**0.5)
    abs_norm_dev_list = (norm_dev_list**2)**0.5
    z_score.extend(abs_norm_dev_list)
  outliers = z_score > zmax
  sel.set_selected(outliers, False)
  return sel

def R_pim_meas(scaler):
  '''Calculate R_pim, R_meas from a scaler'''
  Ihl = scaler.Ih_table.intensities
  gvalues = scaler.Ih_table.inverse_scale_factors

  ones = flex.double([1.0] * len(Ihl))
  nh = ones * scaler.Ih_table.h_index_matrix

  I_average = (((Ihl/gvalues) * scaler.Ih_table.h_index_matrix)/nh)
  I_average_expanded = flex.double(np.repeat(I_average,
    scaler.Ih_table.h_index_counter_array))

  diff = abs((Ihl/gvalues) - I_average_expanded)
  reduced_diff = diff * scaler.Ih_table.h_index_matrix

  selection = (nh != 1.0)
  sel_reduced_diff = reduced_diff.select(selection)
  sel_nh = nh.select(selection)

  Rpim_upper = flex.sum(((1.0/(sel_nh - 1.0))**0.5) * sel_reduced_diff)
  Rmeas_upper = flex.sum(((sel_nh/(sel_nh - 1.0))**0.5) * sel_reduced_diff)
  sumIh = I_average_expanded * scaler.Ih_table.h_index_matrix
  sumIh = sumIh.select(selection)
  Rpim_lower = flex.sum(sumIh)
  Rpim = Rpim_upper/Rpim_lower
  Rmeas = Rmeas_upper/Rpim_lower
  return Rpim, Rmeas

def calc_normE2(reflection_table, experiments):
  '''calculate normalised intensity values for centric and acentric reflections'''
  msg = ('Calculating normalised intensity values. {sep}'
    'Negative intensities are set to zero for the purpose of calculating {sep}'
    'mean intensity values for resolution bins. This is to avoid spuriously {sep}'
    'high E^2 values due to a mean close to zero and should only affect {sep}'
    'the E^2 values of the highest resolution bins. {sep}'
    ).format(sep='\n')
  logger.info(msg)
  u_c = experiments.crystal.get_unit_cell().parameters()
  s_g = experiments.crystal.get_space_group()
  crystal_symmetry = crystal.symmetry(unit_cell=u_c, space_group=s_g)
  miller_set = miller.set(crystal_symmetry=crystal_symmetry,
                          indices=reflection_table['asu_miller_index'])
  reflection_table = reflection_table.select(reflection_table['d'] > 0.0)
  reflection_table['resolution'] = 1.0/reflection_table['d']**2
  #handle negative reflections to minimise effect on mean I values.
  reflection_table['intensity_for_norm'] = copy.deepcopy(reflection_table['intensity'])
  sel = reflection_table['intensity'] < 0.0
  reflection_table['intensity_for_norm'].set_selected(sel, 0.0)
  miller_array = miller.array(miller_set, data=reflection_table['intensity_for_norm'])
  #set up binning objects
  reflection_table['centric_flag'] = miller_array.centric_flags().data()
  n_centrics = reflection_table['centric_flag'].count(True)
  n_acentrics = reflection_table['centric_flag'].count(False)

  if n_acentrics > 20000 or n_centrics > 20000:
    n_refl_shells = 20
  elif n_acentrics > 15000 or n_centrics > 15000:
    n_refl_shells = 15
  elif n_acentrics < 10000:
    reflection_table['Esq'] = flex.double([1.0]*len(reflection_table))
    del reflection_table['intensity_for_norm']
    msg = ('No normalised intensity values were calculated, {sep}'
    'as an insufficient number of reflections were detected. {sep}'
    ).format(sep='\n')
    logger.info(msg)
    return reflection_table
  else:
    n_refl_shells = 10

  #calculate normalised intensities: first calculate bin averages
  step = ((max(reflection_table['resolution']) - min(reflection_table['resolution'])
           + 1e-8) / n_refl_shells)
  if n_centrics:
    centrics_array = miller_array.select_centric()
    centric_binner = centrics_array.setup_binner_d_star_sq_step(d_star_sq_step=step)
    mean_centric_values = centrics_array.mean(use_binning=centric_binner)
    mean_centric_values = mean_centric_values.data[1:-1]
    centric_bin_limits = centric_binner.limits()

  acentrics_array = miller_array.select_acentric()
  acentric_binner = acentrics_array.setup_binner_d_star_sq_step(d_star_sq_step=step)
  mean_acentric_values = acentrics_array.mean(use_binning=acentric_binner)
  mean_acentric_values = mean_acentric_values.data[1:-1]
  acentric_bin_limits = acentric_binner.limits()
  #now calculate normalised intensity values
  reflection_table['Esq'] = flex.double([0.0]*len(reflection_table))
  if n_centrics:
    for i in range(0, len(centric_bin_limits)-1):
      sel1 = reflection_table['centric_flag'] == True
      sel2 = reflection_table['resolution'] > centric_bin_limits[i]
      sel3 = reflection_table['resolution'] <= centric_bin_limits[i+1]
      sel = sel1 & sel2 & sel3
      intensities = reflection_table['intensity'].select(sel)
      reflection_table['Esq'].set_selected(sel, intensities/ mean_centric_values[i])
  for i in range(0, len(acentric_bin_limits)-1):
    sel1 = reflection_table['centric_flag'] == False
    sel2 = reflection_table['resolution'] > acentric_bin_limits[i]
    sel3 = reflection_table['resolution'] <= acentric_bin_limits[i+1]
    sel = sel1 & sel2 & sel3
    intensities = reflection_table['intensity'].select(sel)
    reflection_table['Esq'].set_selected(sel, intensities/ mean_acentric_values[i])
  del reflection_table['intensity_for_norm']
  msg = ('Calculated normalised intensity values. {sep}'
    'The number of centric & acentric reflections is {0} & {1}. {sep}'
    'Intensities were binned into {2} resolution bins. {sep}'
    "Normalised intensities were added to the reflection table as 'Esq'. {sep}"
    ).format(n_centrics, n_acentrics, n_refl_shells, sep='\n')
  logger.info(msg)
  return reflection_table

def calculate_wilson_outliers(reflection_table):
  '''function that takes in a reflection table and experiments object and
  looks at the wilson distribution of intensities in reflection shells to
  look for the presence of outliers with high intensities. Returns a bool
  flex array indicating any outliers.'''
  reflection_table['wilson_outlier_flag'] = flex.bool([False] * len(reflection_table))

  centric_cutoff = 23.91
  sel1 = reflection_table['centric_flag'] == True
  sel2 = reflection_table['Esq'] > centric_cutoff #probability <10^-6
  reflection_table['wilson_outlier_flag'].set_selected(sel1 & sel2, True)

  acentric_cutoff = 13.82
  sel1 = reflection_table['centric_flag'] == False
  sel2 = reflection_table['Esq'] > acentric_cutoff #probability <10^-6
  reflection_table['wilson_outlier_flag'].set_selected(sel1 & sel2, True)
  msg = ('{0} reflections were rejected as outliers based on their normalised {sep}'
    'intensity values. These are reflections that have a probablity of {sep}'
    '< 10e-6 based on a Wilson distribution (E^2 > {1}, {2} for centric {sep}'
    'and acentric reflections respectively). {sep}'
    ).format(reflection_table['wilson_outlier_flag'].count(True), centric_cutoff,
    acentric_cutoff, sep='\n')
  logger.info(msg)
  return reflection_table['wilson_outlier_flag']
