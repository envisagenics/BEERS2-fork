from beers.flowcell_lane import FlowcellLane, LaneCoordinates
from beers.cluster_packet import ClusterPacket
from beers.beers_exception import BeersException
from beers.cluster import Cluster
import gzip
import itertools
import numpy as np
import re
import pysam
import math
import os


class Flowcell:
    """
    The flowcell object is meant to be a singleton object instantiated by the controller since only a single flowcell
    is simulated for any given BEERS run. This object simulates the retention (attachment) of a subset of the
    molecules derived from library prep, and insures that no two retained molecules share the same coordinate set.
    In the process molecules are converted into cluster objects and molecule packets into cluster packets, the
    latter of which is the medium of exchange for the subsequence sequence pipeline steps.
    """

    coords_match_pattern = re.compile(r'^.*:(\w+):(\d+):(\d+):(\d+):(\d+)$')

    def __init__(self, run_id, configuration, parameters):
        """
        A flowcell's geometry (i.e., coordinate ranges for lane, tile, x, y) are either provided via the configuration
        file or derived from the initial FASTQ files applied to the expression portion of the pipeline.  The flowcell
        retention rate is specified as a percentage in the configuration file.  The user may restrict which lanes of
        the flowcell to use via the configuration.  Otherwise, all the lanes described by the flowcell's geometry are
        used.  The individual flowcell lanes to be used are instantiated as FlowcellLane objects, each of which keeps
        track of the coordinates used for its given lane.  Each lane has its own coordinate generator to produce new
        coordinates for each newly retained molecule.
        own coordinate generator.
        :param run_id: The BEERS run id
        :param configuration: The entire configuration file - only used to find the paths to the input FASTQ files
        if needed.
        :param parameters: Parameters specific to the flowcell defined in the configuration file under the
        controller.
        """
        self.run_id = run_id
        self.configuration = configuration
        self.parameters = parameters
        self.flowcell_retention = self.parameters["flowcell_retention_percentage"] / 100
        self.min_coords = {"lane": 10_000, "tile": 10_000, "x": 10_000, "y": 10_000}
        self.max_coords = {"lane": 0, "tile": 0, "x": 0, "y": 0}
        self.set_flowcell_coordinate_ranges()
        self.available_lanes = list(range(self.min_coords['lane'], self.max_coords['lane'] + 1))
        self.lanes_to_use = self.parameters["lanes_to_use"] or self.available_lanes
        self.flowcell_lanes = []
        self.coordinate_generators = {}
        for lane in self.lanes_to_use:
            self.flowcell_lanes.append(FlowcellLane(lane))
            self.coordinate_generators[lane] = self.generate_coordinates(lane)

    def validate(self):
        """
        Insures the the configuration file parameters relating to the flowcell are appropriate.  Otherwise one or more
        error messages are crafted.
        :return: A tuple indicating whether or not the flowcell configuration is valid and a diagnostic message for
        the user.
        """
        valid = True
        msg = ""
        if not set(self.lanes_to_use).issubset(set(self.available_lanes)):
            valid = False
            msg += f"The flowcell lanes to use {self.lanes_to_use} must be a subset of the available lanes" \
                   f" {self.available_lanes}.\n"
        if not self.flowcell_retention or self.flowcell_retention >= 1:
            valid = False
            msg += f"The flowcell retention {self.flowcell_retention} value must be less than 1" \
                   f" (1 signifies total retention)."
        return valid, msg

    def identify_retained_molecules(self, molecule_packet):
        """
        Identifies those molecules in a given molecule packet to be retained on the flowcell by
        sampling without replacement.
        :param molecule_packet: The packet of molecules being applied to the flowcell.
        :return: Those molecules retained by the flowcell.
        """
        number_samples_to_draw = math.floor(self.flowcell_retention * len(molecule_packet.molecules))
        return np.random.choice(molecule_packet.molecules, size=number_samples_to_draw, replace=False)

    def convert_molecule_pkt_to_cluster_pkt(self, molecule_packet):
        """
        Takes the molecule packet containing only retained molecules and evenly apportions those molecules across the
        flowcell lanes available for use (i.e., first group is attached to the first lane to use, the seocnd group to
        the second lane to use and so on).  Each molecule is then used to create a cluster, identified by the lane
        and the tile,x, and y coordinates as provided by the lane's coordinate generator.  The clusters are rolled
        up into a cluster packet, which again picks up the molecule packet's sample information.  So the cluster
        packet, like the molecule packet from which it was derived, also applies to one sample only.
        :param molecule_packet: The molecule packet now containing only those molecule retained by the flowcell.
        :return: A cluster packet containing the retained molecules as cluster objects with a lane and set of
        coordinates identified.
        """
        cluster_packet_id = ClusterPacket.next_cluster_packet_id
        ClusterPacket.next_cluster_packet_id += 1
        clusters = []
        molecules_per_lane = len(molecule_packet.molecules)//len(self.lanes_to_use)
        lane_index = 0
        lane = self.lanes_to_use[lane_index]
        for counter, molecule in enumerate(molecule_packet.molecules):
            cluster_id = Cluster.next_cluster_id
            if (counter + 1) % molecules_per_lane == 0 and lane_index + 1 < len(self.lanes_to_use):
                lane_index += 1
                lane = self.lanes_to_use[lane_index]
            clusters.append(Cluster(self.run_id, cluster_id, molecule, lane, next(self.coordinate_generators[lane])))
            if (counter + 1) % 100 == 0:
                print(f"Assigned flowcell coordinates to {counter + 1} clusters.")
            Cluster.next_cluster_id += 1
        return ClusterPacket(cluster_packet_id, molecule_packet.sample, clusters)

    def load_flowcell(self, molecule_packet):
        """
        The entry point into the flowcell object.  Aside from the instantiation and validation, all other bound methods
        are helper methods.  The portion of the molecules of the molecule packet retained by the flowcell replaces the
        original molecule collection and that diminished molecule packet is converted into a cluster packet, where
        each molecule is converted into a cluster having a unique set of flowcell coordinates.
        :param molecule_packet: incoming molecule packet (from library prep or a simulated library prep output)
        :return: cluster packet contained those retained molecules affixed to the flowcell via unique coordinates.
        """
        retained_molecules = self.identify_retained_molecules(molecule_packet)
        print(f"Number of molecules to be attached to flowcell {len(retained_molecules)}")
        molecule_packet.molecules = retained_molecules
        cluster_packet = self.convert_molecule_pkt_to_cluster_pkt(molecule_packet)
        return cluster_packet

    def generate_coordinates(self, lane):
        """
        A Python generator that randomly selects tile, x, and y coordinates and uses them to create a LaneCoordinates
        object.  The new lane coordinates object is compared with all the consumed coordinates in the given lane.  If
        the object is in the consumed coordinates, it is discarded and the random selection repeated.  Once a selection
        is found to be unique, it is added to the consumed coordinates for the given lane and the object is returned.
        If no set of unique coordinates can be found in 100 attempts, an exception is thrown.
        :param lane: the lane for which a unique combination of tile, x, and y cooredinates is to be selected
        :return: a unique combination of coordinates for the lane given.
        """
        ctr = 0
        while True:
            ctr += 1
            x = np.random.choice(range(self.min_coords['x'], self.max_coords['x'] + 1))
            y = np.random.choice(range(self.min_coords['y'], self.max_coords['y'] + 1))
            tile = np.random.choice(range(self.min_coords['tile'], self.max_coords['tile'] + 1))
            coordinates = LaneCoordinates(tile, x, y)
            consumed_coordinates = [flowcell_lane.consumed_coordinates
                                    for flowcell_lane in self.flowcell_lanes
                                    if flowcell_lane.lane == lane][0]
            if coordinates not in consumed_coordinates:
                ctr = 0
                consumed_coordinates.append(coordinates)
                yield coordinates
            if ctr >= 100:
                raise BeersException("Unable to find unused flowcell coordinates after 100 attempts.")

    @staticmethod
    def generate_coordinates_from_alignment_file(input_file_path):
        """
        This generator depends on a sorted alignment file for coordinate retrieval and is not currently being
        used.
        :return: flowcell coordinates
        """
        input_file = pysam.AlignmentFile(input_file_path, "rb")
        for line in input_file.fetch():
            if line.is_unmapped or not line.is_read1 or line.get_tag(tag="NH") != 1:
                continue
            coords_match = re.match(Flowcell.coords_match_pattern, line.qname)
            if coords_match:
                (flowcell, lane, tile, x, y) = coords_match.groups()
                coordinates = LaneCoordinates(tile, x, y)
                yield coordinates

    def set_flowcell_coordinate_ranges(self):
        """
        Determines the minimum and maximum range for each flowcell coordinate, including the lane.  If the ranges are
        supplied via the configuration file, those ranges (min and max) are rendered as dictionaries.  Otherwise, each
        input FASTQ file is applied to obtain the same ranges.
        """
        geometry = self.parameters['flowcell_geometry']
        if not geometry:
            fastq_file_paths = []
            input_directory_path = self.configuration['controller']['input']['directory_path']
            for fastq_filename in self.configuration['controller']['input']['fastq_filenames']:
                fastq_file_paths.append(os.path.join(input_directory_path, fastq_filename))
            self.get_coordinate_ranges(fastq_file_paths)
        else:
            self.min_coords = {"lane": geometry['min_lane'], "tile": geometry['min_tile'],
                               "x": geometry['min_x'], "y": geometry['min_y']}
            self.max_coords = {"lane": geometry['max_lane'], "tile": geometry['max_tile'],
                               "x": geometry['max_x'], "y": geometry['max_y']}

    def get_coordinate_ranges(self, fastq_file_paths):
        """
        Applied when the flowcell's coordinate ranges are determined by the FASTQ input files.  The coordinates for
        every entry in every file are compared with existing minimums and maximums (which are originally set in the
        flowcell constructor) and those coordinates replace those existing minimums and maximums if appropriate.
        After all entries are read the dictionaries representing the discovered ranges are set.
        :param fastq_file_paths: list of the full paths of every fastq file serving as expression pipeline input.
        """
        min_coords = self.min_coords
        max_coords = self.max_coords
        for fastq_file_path in fastq_file_paths:
            with gzip.open(fastq_file_path, 'rb') as fastq_file:
                for counter, (byte_header, content, toss, scores) in enumerate(
                        itertools.zip_longest(*[fastq_file] * 4)):
                    if (counter + 1) % 1_000_000 == 0:
                        print(f"{counter + 1} reads")
                    header = byte_header.decode()
                    sequence_identifier = header.split(" ")[0]
                    coords_match = re.match(Flowcell.coords_match_pattern, sequence_identifier)
                    if coords_match:
                        (flowcell, lane, tile, x, y) = coords_match.groups()
                        lane, tile, x, y = int(lane), int(tile), int(x), int(y)
                        min_coords['x'] = min(x, min_coords['x'])
                        max_coords['x'] = max(x, max_coords['x'])
                        min_coords['y'] = min(y, min_coords['y'])
                        max_coords['y'] = max(y, max_coords['y'])
                        min_coords['tile'] = min(tile, min_coords['tile'])
                        max_coords['tile'] = max(tile, max_coords['tile'])
                        min_coords['lane'] = min(lane, min_coords['lane'])
                        max_coords['lane'] = max(lane, max_coords['lane'])
        self.min_coords, self.max_coords = min_coords, max_coords
