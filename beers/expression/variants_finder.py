import sys
import argparse
import pysam
import re
from timeit import default_timer as timer
from collections import namedtuple
from operator import attrgetter, itemgetter
import math
from io import StringIO


Read = namedtuple('Read', ['type', 'chromosome', 'position', 'description'])
"""
A named tuple that possesses all the attributes of a variant
type:  match (M), deletion (D), insertion (I)
chromosome: chrN
position: position on ref genome
description: description of the variant (e.g., C, IAA, D5, etc.) 
"""


class VariantsFinder:
    """
    This class creates a text file listing variants for those locations in the reference genome having variants.
    The variants include snps and indels with the number of reads attribute to each variant.
    The text-based input file has no header and the following columns:
    1) CHROMOSOME (column 1 in a SAM file)
    2) START (column 3 in a SAM file)
    3) CIGAR  (column 4 in a SAM file)
    4) SEQ  (column 10 in a SAM file)
    The reads must be sorted by location

    This script outputs a file that gives the full breakdown at each
    location in the genome of the number of A's, C's, G's and T's as
    well as the number of each size of insertion and deletion.
    If it's an insertion the sequence of the insertion itself is given.
    So for example a line of output like the following means
    29 reads had a C in that location, one had a T and
    also one read had an insertion TT and three reads had an insertion TTT
    chr1:10128503 | C:29 | T:1 | IT:1 ITTT:3
    """

    def __init__(self, chromosome, alignment_file, reference_sequence, parameters):
        self.chromosome = chromosome
        self.alignment_file = alignment_file
        self.reference_sequence = reference_sequence
        self.entropy_sort = True if parameters["sort_by_entropy"] else False
        self.depth_cutoff = parameters["cutoff_depth"] or 10
        self.clip_at_start_pattern = re.compile("(^\d+)[SH]")
        self.clip_at_end_pattern = re.compile("\d+[SH]$")
        self.variant_pattern = re.compile("(\d+)([NMID])")
        self.indel_pattern = re.compile("\|([^|]+)")

    def remove_clips(self, cigar, sequence):
        """
        Remove soft and hard clips at the beginning and end of the cigar string and remove soft and hard clips at
        the beginning of the seq as well.  Modified cigar string and sequence are returned
        :param cigar: raw cigar string from read
        :param sequence: raw sequence string from read
        :return: tuple of modified cigar and sequence strings (sans clips)
        """
        clip_at_start = re.search(self.clip_at_start_pattern, cigar)
        if clip_at_start:
            cigar = re.sub(self.clip_at_start_pattern, "", cigar)
            sequence = sequence[int(clip_at_start.group(1)):]
        cigar = re.sub(self.clip_at_end_pattern, "", cigar)
        return cigar, sequence

    def call_variants(self, reads):
        """
        Parses the reads dictionary (read named tuple:read count) for each chromosome - position to create
        a line with the variants and their counts delimited by pipes.  Dumping each chromosome's worth of
        data at a time is done to avoid too sizable a dictionary.  Additionally, if the user requests a sort by entropy,
        this function will do that ordering and send that data to stdout.
        :param reads: dictionary of reads to read counts
        """

        # variants list
        variants = []

        # Initializing the variable that will hold position information objects
        position_info = None

        # This dictionary is only used if the user requests that the read lines be sorted by entropy
        entropy_map = dict()

        # Iterate over the reads in the dictionary of variants to read counts sorted by the read position
        for read in sorted(reads.keys(), key=attrgetter('position')):

            # Initial iteration - set up position information object.
            if not position_info:
                position_info = PositionInfo(read.chromosome, read.position)

            # If the new position differs from the position of the position information currently being
            # consolidated, dump the current position information to the variants file if it is determined to
            # contain at least one variant.  In either case, create a new position information object for the new
            # position.
            if read.position != position_info.position:

                reference_base = self.reference_sequence[position_info.position - 1]
                if position_info.has_variant(reference_base):
                    variants.append(position_info)

                # If the sort by entropy option is selected, also add to the entropy map dictionary the position
                # information entropy, keyed by the line content but only if the total number of reads exceeds the
                # depth cutoff.
                if self.entropy_sort and position_info.get_total_reads() >= int(self.depth_cutoff):
                        entropy_map[position_info.__str__()] = position_info.calculate_entropy()

                position_info = PositionInfo(read.chromosome, read.position)

            # Add the read description and read count to the position information
            position_info.add_read(read.description, reads[read])

        # Now that the reads are exhausted for this chromosome, dump the data from the current position information
        # object to the file.
        reference_base = self.reference_sequence[position_info.position - 1]
        if position_info.has_variant(reference_base):
            variants.append(position_info)

        # If the user selected the sort by entropy option, other the entropy_map entries in descending order
        # of entropy and print to std out.
        if self.entropy_sort:
            sorted_entropies = sorted(entropy_map.items(), key=itemgetter(1), reverse=True)
            for key, value in sorted_entropies:
                print(key, end='')

        return variants

    def collect_reads(self):
        """
        Iterate over the input txt file containing cigar, seq, start location, chromosome for each read and consolidate
        reads for each position on the genome.
        """

        reads = dict()
        for line in self.alignment_file.fetch(self.chromosome):

            # Remove unaligned reads, reverse reads, and non-unique alignments
            if line.is_unmapped or not line.is_read1 or line.get_tag(tag="NH") != 1:
                continue

            # Alignment Seqment reference_start is zero-based - so adding 1 to conform to convention.
            start = line.reference_start + 1
            sequence = line.get_forward_sequence()
            cigar = line.cigarstring
            cigar, sequence = self.remove_clips(cigar, sequence)
            current_pos_in_genome = int(start)
            loc_on_read = 1

            # Iterate over the variant types and lengths in the cigar string
            for match in re.finditer(self.variant_pattern, cigar):
                length = int(match.group(1))
                read_type = match.group(2)

                # Skip over N type reads since these generally represent a read bracketing an intron
                if read_type == "N":
                    current_pos_in_genome += length
                    continue

                # For a match, record all the snps at the each location continuously covered by this read type
                if read_type == "M":
                    stop = current_pos_in_genome + length
                    while current_pos_in_genome < stop:
                        location = current_pos_in_genome
                        reads[Read(read_type, self.chromosome, location, sequence[loc_on_read - 1])] = \
                            reads.get(
                                Read(read_type, self.chromosome, location, sequence[loc_on_read - 1]), 0) + 1
                        loc_on_read += 1
                        current_pos_in_genome += 1
                    continue

                # For a deletion, designate the read named tuple description with a Dn where n is the
                # length of the deletion starting at this position.  In this way, subsequent reads having a
                # deletion of the same length at the same position will be added to this key.
                if read_type == "D":
                    location = current_pos_in_genome
                    reads[Read(read_type, self.chromosome, location, f'D{length}')] = \
                        reads.get(Read(read_type, self.chromosome, location, f'D{length}'), 0) + 1
                    current_pos_in_genome += length
                    continue

                # For an insert, designate the read named tuple description with an Ib+ where b+ are the
                # bases to a inserted starting with this position.  In this way, subsequent reads having an
                # insertion of the same bases at the same position will be added to this key.
                if read_type == "I":
                    location = current_pos_in_genome
                    insertion_sequence = sequence[loc_on_read - 1: loc_on_read - 1 + length]
                    reads[Read(read_type, self.chromosome, location, f'I{insertion_sequence}')] = \
                        reads.get(
                            Read(read_type, self.chromosome, location, f'I{insertion_sequence}'), 0) + 1
                    loc_on_read += length
        return reads

    def find_variants(self):
        return self.call_variants(self.collect_reads())

    @staticmethod
    def main():
        """
        CLI Entry point into the variants_finder program.  Parses the use input, created the VariantsFinder object,
        passing in the arguments and runs the process to find the variants inside a timer.
        """
        parser = argparse.ArgumentParser(description='Find Variants')
        parser.add_argument('-m', '--chromosome',
                            help="Chromosome for which variants are to be found.")
        parser.add_argument('-a', '--alignment_file_path',
                            help="Path to alignment BAM file.")
        parser.add_argument('-g', '--reference_genome_filename',
                            help="Path to the related reference genome fasta file.  Used to eliminate read positions "
                                 "that contain no variants.")
        parser.add_argument('-s', '--sort_by_entropy', action='store_true',
                            help="Optional request to sort line in order of descreasing entropy.")
        parser.add_argument('-c', '--cutoff_depth', type=int, default=10,
                            help="Integer to indicate minimum read depth a position must have for inclusion."
                                 " If the option is not selected, a default of 10 will be applied as the minimum"
                                 " read depth.  Note that this option is used only if the sort_by_entropy option is"
                                 " invoked.")
        args = parser.parse_args()
        print(args)

        alignment_file = pysam.AlignmentFile(args.alignment_file_path, "rb")
        reference_chromosome_sequence = ''
        with open(args.reference_genome_filename) as reference_genome_file:
            building_sequence = False
            sequence = StringIO()
            for line in reference_genome_file:
                if line.startswith(">"):
                    if building_sequence:
                        reference_chromosome_sequence = sequence.getvalue()
                        sequence.close()
                        break
                    identifier = re.sub(r'[ \t].*\n', '', line)[1:]
                    if identifier == args.chromosome:
                        building_sequence = True
                    continue
                elif building_sequence:
                    sequence.write(line.rstrip('\n').upper())
            else:
                print(f'Chromosome {args.chromosome} not found in reference genome supplied.')
                sys.exit(1)

        parameters = {'sort_by_entropy': args.sort_by_entropy, 'cutoff_depth': args.cutoff_depth}

        variants_finder = VariantsFinder(args.chromosome,
                                         alignment_file,
                                         reference_chromosome_sequence,
                                         parameters)
        start = timer()
        variants = variants_finder.find_variants()
        with open("../../data/logs/variants_finder.log", 'w') as log_file:
            for variant in variants:
                log_file.write(variant.__str__())
        end = timer()
        sys.stderr.write(f"Variants Finder: {end - start} sec\n")


class PositionInfo:
    """
    This class is meant to capture all the read data associated with a particular chromsome and position on the
    genome.  It is used to ascertain whether this position actually holds a variant.  If it does, the data is
    formatted into a string to be written into the variants file.
    """

    def __init__(self, chromosome, position):
        self.chromosome = chromosome
        self.position = position
        self.reads = []

    def add_read(self, description, read_count):
        self.reads.append((description, read_count))

    def get_total_reads(self):
        return sum([read[1] for read in self.reads])

    def get_abundances(self):
        return [read[1] / self.get_total_reads() for read in self.reads]

    def calculate_entropy(self):
        """
        Use the top two abundances (if two) of the variants for the given position to compute an entropy.  If
        only one abundance is given, return 0.
        :return: entropy for the given position
        """
        abundances = self.get_abundances()
        if len(abundances) < 2:
            return 0

        # cloning the abundances list since lists are mutable.
        abundances_copy = abundances.copy()

        # Retrieve the highest abundance, then remove it and retrieve the highest abundance again to get the
        # next highest.
        max_abundances = [max(abundances_copy)]
        abundances_copy.remove(max_abundances[0])
        max_abundances.append(max(abundances_copy))

        # In the event that the second abundance reads nearly 0, just return 0
        if max_abundances[1] < 0.0001:
            return 0

        # Use a scale factor to normalize to the two abundances used to calculate entropy
        scale = 1 / sum(max_abundances)
        max_abundances = [scale * max_abundance for max_abundance in max_abundances]
        return -1 * max_abundances[0] * math.log2(max_abundances[0]) - max_abundances[1] * math.log2(max_abundances[1])

    def has_variant(self, reference_base):
        """
        To have a variant, the position information must contain a single read description and that description may
        not be identical to the base at that position in the reference genome.
        :param reference_base: base at this position in the reference genome.
        :return: True if the position information included at least one variant and false otherwise.
        """
        if len(self.reads) > 1 or self.reads[0][0] != reference_base:
            return True
        return False

    def __str__(self):
        """
        Provides a string representation that may be used to dump to a file.
        :return: string representation
        """
        abundances = [str(round(abundance, 2)) for abundance in self.get_abundances()]
        s = StringIO()
        s.write(f'{self.chromosome}:{self.position}')
        for read in self.reads:
            s.write(f' | {read[0]}:{read[1]}')
        s.write(f"\tTOT={self.get_total_reads()}")
        s.write(f"\t{','.join(abundances)}")
        s.write(f"\tE={self.calculate_entropy()}\n")
        return s.getvalue()


if __name__ == "__main__":
    sys.exit(VariantsFinder.main())

'''Example call
python variants_finder.py \
 -m 19 \
 -a ../../data/expression/GRCh38/Test_data.1002_baseline.sorted.bam \
 -g ../../data/expression/GRCh38/Homo_sapiens.GRCh38.reference_genome.fa
'''
