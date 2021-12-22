import sys
import functools

import numpy as np
import scipy.stats

import beers_utils
import beers_utils.molecule
from beers_utils.general_utils import GeneralUtils
import beers
import beers.cluster
import beers.cluster_packet

# Sequence-by-synthesis parameters
PHRED_MIN_ASCII = 33
PHRED_MAX_QUALITY = 41
BASE_ARRAY = np.array([ord(x) for x in beers.cluster.BASE_ORDER], dtype="uint8")

class SequenceBySynthesisStep:

    name = "Sequence By Synthesis Step"

    # Sequencing parameters
    MAX_SKIPS = 10
    MAX_DROPS = 10
    EPSILON = 100 # noise standard deviation
    # Cross-talk between flourescence channels
    # chosen arbitrarily TODO: find a realistic table
    CROSS_TALK = np.array(
        [[1.0, 0.3, 0.2, 0.0],
         [0.1, 1.0, 0.2, 0.1],
         [0.2, 0.1, 1.0, 0.3],
         [0.3, 0.1, 0.1, 1.0]]
        )

    def __init__(self, step_log_file_path, parameters, global_config):
        self.log_filename = step_log_file_path
        self.read_length = parameters['read_length']
        self.forward_is_5_prime = parameters.get('forward_is_5_prime', True)
        if self.forward_is_5_prime != True:
            # TODO: support both fwd and rev reads being the first read
            raise NotImplementedError("Sequencing is only supported with forward_is_5_prime = true")
        self.paired_ends = parameters.get('paired_ends', False)
        if self.paired_ends == False:
            #TODO: support single end reads
            raise NotImplementedError("Sequencing is only supported for paired end data at the moment (paired_end = True)")

        # Sequencing error rate
        self.skip_rate = parameters['skip_rate']
        self.drop_rate = parameters['drop_rate']

        # Determine where the barcodes lie
        self.i5_start = len(global_config['resources']['pre_i5_adapter'])
        self.i7_start = len(global_config['resources']['post_i7_adapter'])
        self.post_i5_length = len(global_config['resources']['post_i5_adapter'])
        # Part read after the i7 is the 'pre' adapter, since reading from 3' end
        # NOTE: + 1 for the additional 'A' that is ligated to 3' end before adapter ligation
        self.post_i7_length = len(global_config['resources']['pre_i7_adapter']) + 1
        i5_barcode_lengths = [len(sample['barcodes']['i5']) for sample in  global_config['samples'].values()]
        assert len(set(i5_barcode_lengths)) == 1
        self.i5_length = i5_barcode_lengths[0]
        i7_barcode_lengths = [len(sample['barcodes']['i7']) for sample in  global_config['samples'].values()]
        assert len(set(i7_barcode_lengths)) == 1
        self.i7_length = i7_barcode_lengths[0]

        # Determine where the sequencing starts
        # Read from the start of the i5 barcode, through the rest of the adatpter and into the read
        # TODO: is this actually how the sequencing is done?
        self.forward_read_start = self.i5_start
        self.forward_read_end = self.forward_read_start + self.i5_length + self.post_i5_length + self.read_length
        self.reverse_read_start = self.i7_start
        self.reverse_read_end = self.reverse_read_start + self.i7_length + self.post_i7_length + self.read_length

        print(f"{SequenceBySynthesisStep.name} instantiated")

    def execute(self, cluster_packet):
        print(f"Starting {self.name}")

        # make an 'estimated' cross-talk matrix TODO: should be estimated from an entire tile
        cross_talk_est = self.CROSS_TALK + np.random.normal(size=self.CROSS_TALK.shape)* 0.01
        cross_talk_inv_est = np.linalg.inv(cross_talk_est)
        # Assume epsilon (flourescence imaging noise) is perfectly known)
        #TODO: epsilon should be estimated from data and can be cycle number dependent
        epsilon_est = self.EPSILON

        for cluster in cluster_packet.clusters:
            # TODO: use self.forward_is_5_prime to know which direction is 'forward'?
            # Perform sequence-by-synthesis and get 'flourescence images'
            forward_flourescence, fwd_start, fwd_cigar, fwd_strand = self.read_flourescence(
                    cluster,
                    self.forward_read_start,
                    self.forward_read_end,
                    direction = "+",
            )
            forward_bases, forward_quality = self.call_bases(forward_flourescence, epsilon_est, cross_talk_inv_est)

            reverse_flourescence, rev_start, rev_cigar, rev_strand = self.read_flourescence(
                    cluster,
                    self.reverse_read_start,
                    self.reverse_read_end,
                    direction = "-",
            )
            reverse_bases, reverse_quality = self.call_bases(reverse_flourescence, epsilon_est, cross_talk_inv_est)

            #Extract barcodes and read sequences
            forward_barcode = forward_bases[:self.i5_length]
            forward_read = forward_bases[self.i5_length + self.post_i5_length:]
            forward_quality = forward_quality[self.i5_length + self.post_i5_length:]

            reverse_barcode = GeneralUtils.create_complement_strand(reverse_bases[:self.i7_length])
            reverse_read = reverse_bases[self.i7_length + self.post_i7_length:]
            reverse_quality = reverse_quality[self.i7_length + self.post_i7_length:]

            cluster.called_sequences = [forward_read, reverse_read]
            cluster.called_barcode = f"{forward_barcode}+{reverse_barcode}"
            cluster.quality_scores = [forward_quality, reverse_quality]

            # Get alignment of read sequences, not including barcodes
            fwd_start, fwd_cigar, fwd_strand = beers_utils.cigar.chain(
                self.i5_length + self.post_i5_length + 1, # 1-based starts
                f"{self.read_length}M",
                "+",
                fwd_start, fwd_cigar, fwd_strand
            )
            rev_start, rev_cigar, rev_strand = beers_utils.cigar.chain(
                self.i7_length + self.post_i7_length, # 1-based starts
                f"{self.read_length}M",
                "+",
                rev_start, rev_cigar, rev_strand
            )
            cluster.read_starts = [fwd_start, rev_start]
            cluster.read_cigars = [fwd_cigar, rev_cigar]
            cluster.read_strands = [fwd_strand, rev_strand]

        cluster_packet.clusters = sorted(cluster_packet.clusters, key=lambda cluster: cluster.coordinates)
        return cluster_packet

    def validate(self):
        print(f"{SequenceBySynthesisStep.name} validating parameters")
        if not self.read_length or self.read_length < 0 or not isinstance(self.read_length, int):
            print(f"The read length, {self.read_length}, is required and must be a non-negative integer,", file=sys.stderr)
            return False
        return True

    def read_flourescence(self, cluster, range_start, range_end, direction="+"):
        """
        Generates flourescence readings from sequence-by-synthesis over the range given
        (specified from 5' to 3' if direction = '+' else from 3' to 5').
        Use call_bases to then get bases and quality scores from the read

        :param cluster: cluster to read by sequence-by-synthesis
        :param range_start:  The starting position (from the 5' end), 0-based
        :param range_end: The ending position (exlusive, from the 5' end), 0-based
        :param direction: '+' if reading from the 5'-3' direction, '-' if reading from 3'-5' direction

        returns flourescence, read_start, read_cigar, read_strand
        """
        read_len = range_end - range_start

        base_counts = cluster.base_counts
        if direction == '-':
            # Take reverse...
            base_counts = base_counts[:, ::-1]
            # ... and complement
            base_counts = base_counts[[3,2,1,0],:] # A<->T, C<->G

        # Grab the bases we care about
        # we go past the end of the read due to the possibility of skips (prephasing)
        base_counts = base_counts[:, range_start:range_end + self.MAX_SKIPS]


        # Pad with zeros for the possibility of skips/drops
        # that get past the start/end of the molecule
        end_pad_len = read_len + self.MAX_SKIPS - len(base_counts)
        prepad_len = self.MAX_DROPS
        padded_base_counts = np.concatenate(
                (   np.zeros((4,prepad_len)),
                    base_counts,
                    np.zeros((4,end_pad_len))
                ),
                axis=1)

        #TODO: this should all be done at the level of a lane or tile or something
        #      various parameters need to be estimated across all clusters

        # Add skips (aka prephasing) where bases are added too quickly in some strands
        # This causes bases to be read too early
        # We assume that each molecule skips at most MAX_SKIPS times
        # And we approximate skips and drops without simulating every molecule
        # individually by just using average molecule counts

        # Each base is equally likely to give a skip
        frac_skipped = get_frac_skipped(self.skip_rate, self.MAX_SKIPS, cluster.molecule_count, read_len)
        # Drops (aka (post)phasing), where bases are not added when they should have been,
        # are done the same as skips
        frac_dropped = get_frac_skipped(self.drop_rate, self.MAX_DROPS, cluster.molecule_count, read_len)

        # 'smear' base counts according to the number of skips
        smeared_base_counts = np.sum( [padded_base_counts[:, prepad_len + skip : prepad_len + read_len + skip] * frac_skipped[:,skip]
                                        for skip in range(1, self.MAX_SKIPS+1)],
                                      axis=0)
        # ...and by the number of drops
        smeared_base_counts = np.sum( [padded_base_counts[:, prepad_len - drop : prepad_len + read_len - drop] * frac_dropped[:,drop]
                                        for drop in range(1, prepad_len+1)],
                                      axis=0)
        # and the ones that neither drop nor skip
        frac_maintained = 1 - (1 - frac_skipped[:, 0]) - (1 - frac_dropped[:,0]) # Neither dropped nor skipped
        smeared_base_counts = padded_base_counts[:, prepad_len:prepad_len + read_len] * frac_maintained

        # Flourescence comes from these after including cross talk between the different colors
        flourescence = self.CROSS_TALK @ (smeared_base_counts + self.EPSILON * np.random.normal(size=smeared_base_counts.shape))

        if direction == "+":
            # 1-based alignement
            align_start = range_start + 1
        else:
            align_start = len(cluster.molecule.sequence) - range_end

        read_start, read_cigar, read_strand = beers_utils.cigar.chain(
            align_start,
            f"{read_len}M",
            direction,
            cluster.molecule.source_start,
            cluster.molecule.source_cigar,
            cluster.molecule.source_strand,
        )
        return flourescence, read_start, read_cigar, read_strand

    def call_bases(self, flourescence, epsilon_est, cross_talk_est_inv):
        '''
        From a flourescence reading, call sequences bases and quality score
        From an approximation of the Bustard algorithm

        :param flourescence: 2d array of shape (4, read_length) with flourescence values
                             for each of the 4 frequencies for each base read
        :param epsilon_est: estimate for EPSILON, the noise size in the flourescence
                            imaging
        :param cross_talk_est_inv: inverse of the estimate of the 4x4 cross talk matrix

        returns called sequence and quality scores (as phred score string)
        '''

        ## We will approximate the Bustard algorithm for calling bases and quality scores
        # see https://www.ncbi.nlm.nih.gov/labs/pmc/articles/PMC2765266/ for a description
        # of Bustard and some of the notation here.
        read_len = flourescence.shape[1]
        # gives probability of a template terminating at position j after t cycles
        phasing_matrix_inv = get_inv_phasing_matrix(read_len, self.skip_rate, self.drop_rate)
        base_counts_est = cross_talk_est_inv @ flourescence @ phasing_matrix_inv
        base_orders = np.argsort(base_counts_est, axis=0)
        called_base = base_orders[-1,:] # bases as nums 0,1,2,3 = ACGT
        second_best_base = base_orders[-2,:] # bases as nums 0,1,2,3 = ACGT
        M = (cross_talk_est_inv * epsilon_est) * (cross_talk_est_inv * epsilon_est).T
        highest_base_count = base_counts_est[called_base, np.arange(read_len)]
        second_base_count = base_counts_est[second_best_base, np.arange(read_len)]
        diff = highest_base_count - second_base_count
        contrasts = np.zeros(shape=(read_len, 4, 1))
        contrasts[np.arange(read_len), called_base] = 1
        contrasts[np.arange(read_len), second_best_base] = -1
        sigma = np.sqrt(contrasts.transpose(0,2,1) @ M @ contrasts)
        prob = scipy.stats.norm.sf(np.abs(diff) / sigma.flatten()) * 2 # two-tailed

        score = (-10 * np.log10(prob)).astype("uint8")
        score = np.minimum(PHRED_MAX_QUALITY, score)
        qual = (PHRED_MIN_ASCII + score).tobytes().decode()
        seq = BASE_ARRAY[called_base].tobytes().decode() # as string "ACGT..." 

        return seq, qual

@functools.lru_cache(maxsize=None)
def get_inv_phasing_matrix(read_len, skip_rate, drop_rate):
    ''' Computes the inverse of Q, the phasing matrix

    The (j,t) entry of Q gives the probability of a template
    terminating at position j after t cycles

    Cached for speed-ups. Since reads are all the same length, they all get the same
    matrix and caching is very useful.
    '''
    phasing_matrix = np.array([[(1 -  skip_rate - drop_rate) if j == t else
                                    (skip_rate**(j - t)*(1-skip_rate) if j > t else
                                     drop_rate**(t - j)*(1-drop_rate))
                                for t in range(read_len)]
                                for j in range(read_len)])
    # Strangely, computing this inverse is very slow on cluster (~3 seconds)
    # even for small size (100x100), though very fast on my laptop (~3ms)
    phasing_mat_inv = np.linalg.inv(phasing_matrix)
    return phasing_mat_inv

def get_frac_skipped(rate, max_skips, molecule_count, read_len):
    ''' Return an array of length (max_skips, read_len) that indicate how many
    of the `molecule_count` molecules have incurred exactly `i` skips by the `j`th
    base in (i,j) element'''
    skips = np.random.random(size = (molecule_count, read_len)) < rate
    num_skips_so_far = np.minimum(np.cumsum(skips, axis=1), max_skips)
    # Count number of molecules that have had exactly k skips at the nth base, for k= 0,1,...MAX_SKIPS
    num_mols_skipped = np.count_nonzero((num_skips_so_far[:,:,None] == np.arange(0, max_skips+1)[None,None,:]), axis=0)
    frac_skipped = num_mols_skipped / molecule_count
    return frac_skipped

if __name__ == "__main__":
    # Test run of SBS
    clusters = []
    for i in range(100):
        seq = "ACGT"*100
        encoded = np.frombuffer(seq.encode("ascii"), dtype='uint8')
        base_counts = np.array([encoded == ord(nt) for nt in beers.cluster.BASE_ORDER]).astype(int) * 1024
        molecule = beers_utils.molecule.Molecule(
                molecule_id = i,
                sequence = seq,
                start = 1,
                source_start = 10,
                source_cigar = "400M",
                source_strand = "+",
                source_chrom = "chr2",
            )
        cluster = beers.cluster.Cluster(
            run_id = 5,
            cluster_id = i,
            lane = 1,
            coordinates = (i,i),
            molecule = molecule,
            molecule_count = 1024,
            base_counts = base_counts)
        clusters.append(cluster)
    cluster_packet = beers.cluster_packet.ClusterPacket(5, None, clusters)
    seq_by_synth = SequenceBySynthesisStep("",
        {
            "forward_is_5_prime": True,
            "read_length": 100,
            "paired_ends": True,
            "skip_rate": 0.002,
            "drop_rate": 0.002
        },
        {"resources": {
                "pre_i5_adapter": "AATGATACGGCGACCACCGAGATCTACAC",
                "post_i5_adapter":  "ACACTCTTTCCCTACACGACGCTCTTCCGATCT",
                "pre_i7_adapter": "GATCGGAAGAGCACACGTCTGAACTCCAGTCAC",
                "post_i7_adapter":  "ATCTCGTATGCCGTCTTCTGCTTG"
            },
        "samples": {
                "sample1": {
                    "barcodes": {
                        "i5": "AGCGCTAG",
                        "i7": "AACCGCGG"
                    }
                },
                "sample2": {
                    "barcodes": {
                        "i5": "GATATCGA",
                        "i7": "TTATAACC"
                    }
                }
            }
        }
    )

    seq_by_synth.execute(cluster_packet)
