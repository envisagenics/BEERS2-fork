from beers.flowcell_lane import FlowcellLane, LaneCoordinates
from beers.cluster_packet import ClusterPacket
from beers.molecule_packet import MoleculePacket
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

    coords_match_pattern = "^.*:(\w+):(\d+)\:(\d+)\:(\d+)\:(\d+)$"

    def __init__(self, configuration, parameters):
        self.configuration = configuration
        self.parameters = parameters
        self.flowcell_retention = self.parameters["flowcell_retention_percentage"] / 100
        self.set_flowcell_coordinate_ranges()
        self.available_lanes = list(range(self.min_coords['lane'], self.max_coords['lane'] + 1))
        self.lanes_to_use = self.parameters["lanes_to_use"] or self.available_lanes
        self.flowcell_lanes = []
        for lane in self.lanes_to_use:
            self.flowcell_lanes.append(FlowcellLane(lane))
        self.coordinate_generator = self.generate_coordinates()

    def validate(self):
        valid = True
        msg = None
        if not set(self.lanes_to_use).issubset(set(self.available_lanes)):
            valid = False
            msg = f"The flowcell lanes to use {self.lanes_to_use} must be a subset of the available lanes" \
                  f" {self.available_lanes}.\n"
        if not self.flowcell_retention or self.flowcell_retention >= 1:
            valid = False
            msg = f"The flowcell retention {self.flowcell_retention} value must be less than 1" \
                  f" (1 signifies total retention)."
        return valid, msg

    def identify_retained_molecules(self, molecule_packet):
        number_samples_to_draw = math.floor(self.flowcell_retention * len(molecule_packet.molecules))
        return np.random.choice(molecule_packet.molecules, size=number_samples_to_draw, replace=False)

    @staticmethod
    def convert_molecule_pkt_to_cluster_pkt(molecule_packet):
        clusters = []
        for molecule in molecule_packet.molecules:
            cluster_id = Cluster.next_cluster_id
            clusters.append(Cluster(cluster_id, molecule))
            Cluster.next_cluster_id += 1
        return ClusterPacket(molecule_packet.sample, clusters)

    def load_flowcell(self, molecule_packet):
        retained_molecules = self.identify_retained_molecules(molecule_packet)
        retained_molecule_packet = MoleculePacket(MoleculePacket.next_molecule_packet_id,
                                                  molecule_packet.sample, retained_molecules)
        MoleculePacket.next_molecule_packet_id += 1
        cluster_packet = self.convert_molecule_pkt_to_cluster_pkt(retained_molecule_packet)
        for cluster in cluster_packet.clusters:
            cluster.assign_coordinates(next(self.coordinate_generator))
        return cluster_packet

    def generate_coordinates(self):
        ctr = 0
        while True:
            ctr += 1
            x = np.random.choice(range(self.min_coords['x'], self.max_coords['x'] + 1))
            y = np.random.choice(range(self.min_coords['y'], self.max_coords['y'] + 1))
            tile = np.random.choice(range(self.min_coords['tile'], self.max_coords['tile'] + 1))
            lane = np.random.choice(self.lanes_to_use)
            coordinates = LaneCoordinates(lane, tile, x, y)
            consumed_coordinates = [flowcell_lane.consumed_coordinates
                                    for flowcell_lane in self.flowcell_lanes
                                    if flowcell_lane.lane == lane][0]
            if coordinates not in consumed_coordinates:
                consumed_coordinates.append(coordinates)
                yield coordinates
            if ctr >= 100:
                raise BeersException("Unable to find unused flowcell coordinates after 100 attempts.")

    def generate_coordinates_from_alignment_file(self):
        '''
        This generator depends on a sorted alignment file for coordinate retrieval and is not currently being
        used.
        :return: flowcell coordinates
        '''
        input_file = pysam.AlignmentFile(self.input_file_path, "rb")
        for line in input_file.fetch():
            if line.is_unmapped or not line.is_read1 or line.get_tag(tag="NH") != 1:
                continue
            coords_match = re.match(Flowcell.coords_match_pattern, line.qname)
            if coords_match:
                (flowcell, lane, tile, x, y) = coords_match.groups()
                coordinates = FlowcellLane.LaneCoordinates(tile, x, y)
                yield coordinates

    def set_flowcell_coordinate_ranges(self):
        geometry = self.parameters['flowcell_geometry']
        if not geometry:
            fastq_file_paths = []
            input_directory_path = self.configuration['controller']['input']['directory_path']
            for fastq_filename in self.configuration['controller']['input']['fastq_filenames']:
                fastq_file_paths.append(os.path.join(input_directory_path, fastq_filename))
            self.min_coords, self.max_coords = Flowcell.get_coordinate_ranges()
        else:
            self.min_coords = {"lane": geometry['min_lane'], "tile": geometry['min_tile'],
                          "x": geometry['min_x'], "y": geometry['min_y']}
            self.max_coords = {"lane": geometry['max_lane'], "tile": geometry['max_tile'],
                          "x": geometry['max_x'], "y": geometry['max_y']}

    def get_coordinate_ranges(self, fastq_file_paths):
        min_coords = {"lane": 10000, "tile": 10000, "x": 10000, "y": 10000}
        max_coords = {"lane": 0, "tile": 0, "x": 0, "y": 0}
        for fastq_file_path in fastq_file_paths:
            with gzip.open(fastq_file_path, 'rb') as fastq_file:
                for counter, (byte_header, content, toss, scores) in enumerate(
                        itertools.zip_longest(*[fastq_file] * 4)):
                    if counter % 1000000 == 0:
                        print(f"{counter} reads")
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
