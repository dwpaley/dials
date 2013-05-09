/*
 * discriminator_strategy.cc
 *
 *  Copyright (C) 2013 Diamond Light Source
 *
 *  Author: James Parkhurst
 *
 *  This code is distributed under the BSD license, a copy of which is
 *  included in the root directory of this package.
 */
#include <boost/python.hpp>
#include <boost/python/def.hpp>
#include <dials/algorithms/background/discriminator_strategy.h>

namespace dials { namespace algorithms { namespace boost_python {

  using namespace boost::python;

  void export_discriminator_strategy()
  {
    class_<DiscriminatorStrategy, boost::noncopyable>(
        "DiscriminatorStrategy", no_init)
      .def("__call__", &DiscriminatorStrategy::operator());
  }

}}} // namespace = dials::algorithms::boost_python
