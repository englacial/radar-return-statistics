# Radar Data Citations

Requested citations and acknowledgments for the underlying radar data, covering
the union of all seasons in the icechunk stores (antarctica, greenland, ase,
utig, crosssystem). Compiled 2026-08-07 from the data providers' citation
pages; verify against the linked sources before publication, as providers
occasionally update grant lists and citation formats.

Seasons covered:

| Season(s) | Program / operator | Instrument | See section |
|---|---|---|---|
| 2013, 2014, 2016, 2017, 2018, 2019 Greenland_P3 | NASA Operation IceBridge (CReSIS) | MCoRDS | 1, 2 |
| 2012, 2014, 2016, 2018 Antarctica_DC8 | NASA Operation IceBridge (CReSIS) | MCoRDS | 1, 2 |
| 2013_Antarctica_P3, 2017_Antarctica_P3 | NASA Operation IceBridge (CReSIS) | MCoRDS | 1, 2 |
| 2019_Antarctica_GV | NASA Operation IceBridge (CReSIS) | MCoRDS | 1, 2 |
| 2013_Antarctica_Basler, 2017_Antarctica_Basler | CReSIS Basler surveys | CReSIS RDS | 1 |
| 2008_Antarctica_BaslerJKB | UTIG ICECAP | HiCARS 1 | 3 |
| 2017_Antarctica_BaslerJKB | UTIG ICECAP-2 era | HiCARS 2 | 3 |
| 2022, 2023 Antarctica_BaslerMKB | NSF COLDEX | MARFA | 4 |

## 1. Open Polar Radar / CReSIS (all seasons)

All data were accessed through [Open Polar Radar](https://data.cresis.ku.edu/)
(via the xopr library). OPR/CReSIS request the following.

Data citation (replace radar name as appropriate, e.g. "RDS"):

> CReSIS. 2024. RDS Data, Lawrence, Kansas, USA. Digital Media.
> http://data.cresis.ku.edu/.

Requested acknowledgment for CReSIS data:

> We acknowledge the use of data and/or data products from CReSIS generated
> with support from the University of Kansas, NASA Operation IceBridge grant
> NNX16AH54G, NSF grants ACI-1443054, OPP-1739003, and IIS-1838230, Lilly
> Endowment Incorporated, and Indiana METACyt Initiative.

OPR toolbox (the software that produced the CSARP products):

> Open Polar Radar. (2024). opr (Version 3.0.1) [Computer software].
> https://gitlab.com/openpolarradar/opr/. https://doi.org/10.5281/zenodo.5683959

(Note: the Zenodo record behind that DOI is titled "CReSIS Toolbox version
3.01" — the opr toolbox's predecessor name; the citation format above is what
OPR's own site requests alongside this DOI.)

Requested acknowledgment for the OPR toolbox:

> We acknowledge the use of software from Open Polar Radar generated with
> support from the University of Kansas, NASA grants 80NSSC20K1242 and
> 80NSSC21K0753, and NSF grants OPP-2027615, OPP-2019719, OPP-1739003,
> IIS-1838230, RISE-2126503, RISE-2127606, and RISE-2126468.

Contact for citation questions: opr@openpolarradar.org.

## 2. NASA Operation IceBridge MCoRDS (P3 / DC8 / GV seasons)

The Greenland P3 (2013–2019), Antarctica DC8 (2012–2018), Antarctica P3
(2013, 2017), and Antarctica GV (2019) seasons were flown as NASA Operation
IceBridge campaigns with the CReSIS MCoRDS radar. The archival NSIDC datasets:

L1B radar echograms ([IRMCR1B v2](https://nsidc.org/data/irmcr1b/versions/2)):

> Paden, J., Li, J., Leuschen, C., Rodriguez-Morales, F. & Hale, R. (2014).
> IceBridge MCoRDS L1B Geolocated Radar Echo Strength Profiles, Version 2
> (IRMCR1B). Boulder, Colorado USA. NASA National Snow and Ice Data Center
> Distributed Active Archive Center. https://doi.org/10.5067/90S1XZRBAX5N

L2 surface/bottom/thickness ([IRMCR2 v1](https://nsidc.org/data/irmcr2/versions/1)) —
relevant since the layer picks used here derive from the same picks:

> Paden, J., Li, J., Leuschen, C., Rodriguez-Morales, F. & Hale, R. (2010).
> IceBridge MCoRDS L2 Ice Thickness, Version 1 (IRMCR2). Boulder, Colorado
> USA. NASA National Snow and Ice Data Center Distributed Active Archive
> Center. https://doi.org/10.5067/GDQ0CUCVTE2Q

Also acknowledge NASA Operation IceBridge for these campaigns. The
2013/2017 Antarctica_Basler seasons are CReSIS-operated surveys distributed
through OPR; they are covered by the CReSIS citation/acknowledgment in
section 1.

## 3. UTIG ICECAP / HiCARS (BaslerJKB seasons)

2008_Antarctica_BaslerJKB (HiCARS 1 era) and 2017_Antarctica_BaslerJKB
(HiCARS 2 era; 3 frames from January 2018) were collected by the University
of Texas Institute for Geophysics ICECAP program.

HiCARS 1 ([IR1HI1B v1](https://nsidc.org/data/ir1hi1b/versions/1)):

> Blankenship, D. D., Kempf, S. D., Young, D. A., Richter, T. G., Schroeder,
> D. M., Greenbaum, J. S., van Ommen, T., Warner, R. C., Roberts, J. L.,
> Young, N. W., Lemeur, E., Siegert, M. J. & Holt, J. W. (2017). IceBridge
> HiCARS 1 L1B Time-Tagged Echo Strength Profiles, Version 1 (IR1HI1B).
> https://doi.org/10.5067/W2KXX0MYNJ9G

HiCARS 2 ([IR2HI1B v1](https://nsidc.org/data/ir2hi1b/versions/1)); note the
January 2018 flights postdate the NSIDC archive's coverage and are
distributed via OPR:

> Blankenship, D. D., Kempf, S. D., Young, D. A., Richter, T. G., Schroeder,
> D. M., Ng, G., Greenbaum, J. S., van Ommen, T., Warner, R. C., Roberts,
> J. L., Young, N. W., Lemeur, E. & Siegert, M. J. (2017). IceBridge HiCARS 2
> L1B Time-Tagged Echo Strength Profiles, Version 1 (IR2HI1B).
> https://doi.org/10.5067/0I7PFBVQOGO5

ICECAP acknowledgment (per the NSIDC dataset documentation): data were
collected by the International Collaborative Exploration of the Cryosphere
through Airborne Profiling (ICECAP) project, funded by NSF, the Antarctic
Climate and Ecosystems Collaborative Research Centre, and NERC, with
additional support from NASA Operation IceBridge.

## 4. NSF COLDEX / MARFA (BaslerMKB seasons)

2022_Antarctica_BaslerMKB and 2023_Antarctica_BaslerMKB are the NSF Center
for Oldest Ice Exploration (COLDEX) airborne seasons (CXA1 2022/23, CXA2
2023/24) with the UTIG MARFA radar. Citations from the
[COLDEX published datasets page](https://coldex.org/published-datasets):

Raw radar data (USAP Data Center):

> Young, D. A., Blankenship, D. D., Buhl, D., Chan, K., Greenbaum, J.,
> Kempf, S. D., et al. (2024). NSF COLDEX Raw MARFA Ice Penetrating Radar
> data. U.S. Antarctic Program (USAP) Data Center.
> https://doi.org/10.15784/601768

OPR-processed radargrams (Texas Data Repository):

> Young, D. A., Paden, J., Greenbaum, J., Blankenship, D. D., Kerr, M.,
> Singh, S., Kaundinya, S., Chan, K., Chan, R., Ng, G., & Kempf, S. D.
> (2024). COLDEX Open Polar Radar MARFA Airborne Radar Data. Texas Data
> Repository. https://doi.org/10.18738/T8/J38CO5

(Related: COLDEX VHF MARFA Open Polar Radar radargrams,
https://doi.org/10.18738/T8/NEF2XM.)

Acknowledge the NSF Science and Technology Center award supporting COLDEX
(NSF OPP-2019719, which also appears in the OPR grant list in section 1).

## 5. Optional instrument references

Commonly cited instrument papers for the radars involved:

- MCoRDS: Rodriguez-Morales, F., et al. (2014). Advanced Multifrequency Radar
  Instrumentation for Polar Research. IEEE Transactions on Geoscience and
  Remote Sensing, 52(5), 2824–2842. https://doi.org/10.1109/TGRS.2013.2266415
- HiCARS: Peters, M. E., Blankenship, D. D., & Morse, D. L. (2005). Analysis
  techniques for coherent airborne radar sounding: Application to West
  Antarctic ice streams. Journal of Geophysical Research, 110, B06303.
  https://doi.org/10.1029/2004JB003222

All DOIs and links in this document were verified against their registrars'
metadata (DataCite/Crossref content negotiation) and live pages on 2026-08-07.
