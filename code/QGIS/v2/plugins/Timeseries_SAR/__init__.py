# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Timeseries_SAR
                                 A QGIS plugin
 Plot timeseries for SAR backscatter data
                             -------------------
        begin                : 2016-01-26
        copyright            : (C) 2016 by Earth Big Data LLC
        email                : kitsin@earthbigdata.com
        git sha              : $Format:%H$
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
 This script initializes the plugin, making it known to QGIS.
"""


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load Timeseries_SAR class from file Timeseries_SAR.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    #
    from .Timeseries_SAR import Timeseries_SAR
    return Timeseries_SAR(iface)
