import time as time
import pickle
from beers.molecule import Molecule

class GeneralUtils:

    @staticmethod
    def generate_seed():
        """
        Provides an integer of 32 bits or less using a seconds based timestamp.  If the timestamp exceeds 32 bits, the
        integer obtained from the lowest 32 bits is returned
        :return: a 32 bit integer seed for the numpy random number generator.
        """
        candidate_seed = int(time.time())

        # If the timestamp bit length exceeds 32 bits, mask out all but the lowest 32 bits.
        if candidate_seed.bit_length() > 32:
            candidate_seed = candidate_seed & 0xffffffff
        return candidate_seed

    @staticmethod
    def reset_molecule_ids(sample_file_path):
        with open(sample_file_path, 'rb') as sample_file:
            molecules = list(pickle.load(sample_file))
        for molecule in molecules:
            molecule.molecule_id = Molecule.next_molecule_id
            Molecule.next_molecule_id += 1
        with open(sample_file_path, "wb") as sample_file:
            pickle.dump(molecules, sample_file)

if __name__ == "__main__":
    GeneralUtils.reset_molecule_ids("/home/crislawrence/Documents/beers_project/BEERS2.0/data/library_prep/molecules.pickle")