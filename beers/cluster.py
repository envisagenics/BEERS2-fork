from collections import namedtuple
import numpy as np
from io import StringIO
import math
import re
from contextlib import closing
from beers.utilities.general_utils import GeneralUtils
from beers.flowcell_lane import LaneCoordinates
from beers.molecule import Molecule
from beers.utilities.adapter_generator import AdapterGenerator, Adapter


BaseCounts = namedtuple('BaseCounts', ["G", "A", "T", "C"])

class Cluster:

    next_cluster_id = 1  # Static variable for creating increasing cluster id's

    MIN_ASCII = 33
    MAX_QUALITY = 41

    def __init__(self, run_id, cluster_id, molecule, lane, coordinates, molecule_count=1, diameter=0,
                 called_sequences=None, called_indices=None, quality_scores=None, base_counts=None,
                 forward_is_5_prime=True):
        self.lane = lane
        self.coordinates = coordinates
        self.cluster_id = cluster_id
        self.run_id = run_id
        self.molecule = molecule
        self.diameter = diameter
        self.molecule_count = molecule_count
        self.forward_is_5_prime = forward_is_5_prime
        self.quality_scores = quality_scores or []
        self.called_sequences = called_sequences or []
        self.called_indices = called_indices or []
        encoded = np.frombuffer(molecule.sequence.encode("ascii"), dtype='uint8')
        counts = {nt: (encoded == ord(nt)).astype('int32') for nt in "ACGT"}
        if base_counts:
            self.base_counts = base_counts
        else:
            self.base_counts = BaseCounts(counts['G'],counts['A'], counts['T'], counts['C'])
        self.forward_is_5_prime = forward_is_5_prime

    def assign_coordinates(self, coordinates):
        self.coordinates = coordinates

    def generate_fasta_header(self, direction):
        if self.forward_is_5_prime and direction == 'f':
            self.header = f"{self.encode_sequence_identifier()}\t{self.called_indices[0]}"

    def encode_sequence_identifier(self):
        # TODO the 1 is a placeholder for flowcell.  What should we do with this?
        return f"@BEERS:{self.run_id}:1:{self.lane}:{self.coordinates.tile}:{self.coordinates.x}:{self.coordinates.y}"

    def set_forward_direction(self, forward_is_5_prime):
        self.forward_is_5_prime = forward_is_5_prime

    def read(self, read_length, paired_ends, barcode_data, adapter_sequences):
        if self.forward_is_5_prime:
            self.read_in_5_prime_direction(read_length, barcode_data, adapter_sequences[0])
            if paired_ends:
                self.read_in_3_prime_direction(read_length, barcode_data, adapter_sequences[1])
        else:
            self.read_in_3_prime_direction(read_length, barcode_data,  adapter_sequences[1])
            if paired_ends:
                self.read_in_5_prime_direction(read_length, barcode_data, adapter_sequences[0])

    def read_over_range(self, range_start, range_end):
        with closing(StringIO()) as called_bases:
            with closing(StringIO()) as quality_scores:
                for position in range(range_start, range_end):
                    base_counts = list(zip('GATC', self.get_base_counts_by_position(position)))
                    max_base_count = max(base_counts, key=lambda base_count: base_count[1])
                    number_max_values = len([base_count[0] for base_count in base_counts
                                        if base_count[1] == max_base_count[1]])
                    if number_max_values > 1:
                        called_bases.write('N')
                        quality_scores.write(str(chr(Cluster.MIN_ASCII)))
                    else:
                        other_bases_total_count = sum(count for base, count in base_counts if base != max_base_count[0])
                        prob = other_bases_total_count / self.molecule_count
                        quality_value = min(Cluster.MAX_QUALITY, math.floor(-10 * math.log10(prob))) \
                                        if prob != 0 else Cluster.MAX_QUALITY
                        called_bases.write(max_base_count[0])
                        quality_scores.write(str(chr(quality_value + Cluster.MIN_ASCII)))
                return called_bases.getvalue(), quality_scores.getvalue()

    def read_in_5_prime_direction(self, read_length, barcode_data, adapter_sequence):
        range_start = barcode_data[0]
        range_end = barcode_data[0] + barcode_data[1]
        called_index, _ = self.read_over_range(range_start, range_end)
        range_start = len(adapter_sequence)
        range_end = range_start + read_length
        called_bases, quality_scores = self.read_over_range(range_start, range_end)
        self.quality_scores.append(quality_scores)
        self.called_sequences.append(called_bases)
        self.called_indices.append(called_index)

    def read_in_3_prime_direction(self, read_length, barcode_data, adapter_sequence):
        range_end = len(self.molecule.sequence) - barcode_data[2]
        range_start = range_end - barcode_data[3]
        called_index, _ = self.read_over_range(range_start, range_end)
        range_end = len(self.molecule.sequence) - len(adapter_sequence)
        range_start = range_end - read_length
        called_bases, quality_scores = self.read_over_range(range_start, range_end)
        quality_scores.reverse()
        called_bases = GeneralUtils.create_complement_strand(called_bases)
        self.quality_scores.append(quality_scores.getvalue())
        self.called_sequences.append(called_bases.getvalue())
        self.called_indices.append(called_index)

    def get_base_counts_by_position(self, index):
        return  (self.base_counts.G[index],
                 self.base_counts.A[index],
                 self.base_counts.T[index],
                 self.base_counts.C[index])

    def __str__(self):
        header = f"run id: {self.run_id}, cluster_id: {self.cluster_id}, molecule_id: {self.molecule.molecule_id}, " \
                 f"molecule_count: {self.molecule_count}, lane: {self.lane}, coordinates: {self.coordinates}\n"
        for index in range(len(self.called_sequences)):
            header += f"called sequence: {self.called_sequences[index]}\n"
            header += f"quality score: {self.quality_scores[index]}\n"
        with closing(StringIO()) as output:
            output.write("pos\tG\tA\tT\tC\torig\n")
            for index in range(len(self.molecule.sequence)):
                output.write(f"{index}\t")
                [output.write(f"{base_count}\t") for base_count in self.get_base_counts_by_position(index)]
                output.write(f"{self.molecule.sequence[index]}\n")
            return header + output.getvalue()

    def serialize(self):
        output = f"#{self.cluster_id}\t{self.run_id}\t{self.molecule_count}\t{self.diameter}\t{self.lane}\t" \
                 f"{self.forward_is_5_prime}\n"
        output += f"#{self.coordinates.serialize()}\n#{self.molecule.serialize()}\n"
        for index in range(len(self.called_sequences)):
            output += f"##{self.called_sequences[index]}\t{self.called_indices[index]}\t{self.quality_scores[index]}\n"
        with closing(StringIO()) as counts:
            for index in range(len(self.molecule.sequence)):
                [counts.write(f"{base_count}\t") for base_count in self.get_base_counts_by_position(index)]
                counts.write("\n")
            output += counts.getvalue()
        return output

    @staticmethod
    def deserialize(data):
        data = data.rstrip()
        called_sequences = []
        called_indices = []
        quality_scores = []
        G_counts = []
        A_counts = []
        T_counts = []
        C_counts = []
        for line_number, line in enumerate(data.split("\n")):
            if line.startswith("##"):
                called_sequence, called_index, quality_score = line[2:].rstrip().split("\t")
                called_sequences.append(called_sequence)
                called_indices.append(called_index)
                quality_scores.append(quality_score)
            elif line.startswith("#"):
                if line_number == 0:
                    cluster_id, run_id, molecule_count, diameter, lane, forward_is_5_prime \
                        = line[1:].rstrip().split("\t")
                if line_number == 1:
                    coordinates = LaneCoordinates.deserialize(line[1:].rstrip())
                if line_number == 2:
                    molecule = Molecule.deserialize(line[1:].rstrip())
            else:
                G_count, A_count, T_count, C_count = line.rstrip().split("\t")
                G_counts.append(int(G_count))
                A_counts.append(int(A_count))
                T_counts.append(int(T_count))
                C_counts.append(int(C_count))
        base_counts = BaseCounts(G_counts, A_counts, T_counts, C_counts)
        return Cluster(int(run_id), cluster_id, molecule, int(lane), coordinates, int(molecule_count), int(diameter),
                       called_sequences, called_indices, quality_scores, base_counts, forward_is_5_prime)
