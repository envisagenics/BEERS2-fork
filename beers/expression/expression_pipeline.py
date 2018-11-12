import sys
import json
import os
import numpy as np
from beers.expression.variants_finder import VariantsFinder
from beers.expression.beagle import Beagle
from beers.utilities.expression_utils import ExpressionUtils
from beers.utilities.general_utils import GeneralUtils
from beers.sample import Sample

class ExpressionPipeline:
    def __init__(self, configuration):
        self.reference_genome = dict()
        pipeline_configuration = configuration['expression_pipeline']
        input_directory_path = pipeline_configuration["input"]["directory_path"]
        self.reference_genome_file_path = \
            rmos.path.join(input_directory_path, pipeline_configuration["input"]["data"]["reference_genome"])

        self.samples = []
        for index, entry in enumerate(pipeline_configuration["input"]["data"]["alignment"]):
            sample_name = os.path.splitext(entry["file"])[0]
            alignment_file_path = os.path.join(input_directory_path, entry["file"])
            self.samples.append(Sample(index, sample_name, alignment_file_path, entry.get("gender", None)))

        try:
            self.output_directory_path = pipeline_configuration["output"]["directory_path"]
        except FileExistsError:
            pass
        self.parameters = {}
        for item in pipeline_configuration["processes"]:
            self.parameters[item["class_name"]] = item.get("parameters", dict())

    def validate(self):
        pass

    def execute(self):
        print("Execution of the Expression Pipeline Started...")

        self.reference_genome = ExpressionUtils.create_reference_genome(self.reference_genome_file_path)
        #for chromosome,sequence in self.reference_genome.items():
        #    print(chromosome, sequence[:25])

        for sample in self.samples:

            variants_finder = \
                VariantsFinder('X',
                               sample.alignment_file_path,
                               self.reference_genome,
                               self.parameters["VariantsFinder"],
                               self.output_directory_path)
            inferred_gender = variants_finder.find_variants()
            sample.gender = sample.gender or inferred_gender

            # Variants to VCF conversion goes here.

        for sample in self.samples:

            beagle = Beagle(self.parameters["Beagle"])
            outcome = beagle.execute()
            if outcome != 0:
                sys.stderr.write("Beagle process failed.\n")
                sys.exit(1)

            #genome_maker = GenomeMaker(chromosome, variants, reference_sequence, self.parameters["GenomeMaker"])
            #genomes = genome_maker.make_genomes()
            #variants = None  # Should we dereference objects no longer needed to save memory?
            #annotation_updates = []
            #transcript_distributions = []
            molecules = []
            #for item in genomes:
            #    indel_data = item[1]
            #    annotation_updater = UpdateAnnotationForGenome(indel_data, self.annotation_filename)
            #    annotation_updates.append(annotation_updater.update_annotation())
            #for _ in range(2):
            #    quantifier = Quantify(annotation_updates, self.alignment_filename)
            #    transcript_distributions.append(quantifier.quantify())
            #for i, item in enumerate(genomes):
            #    genome = item[0]
            #    transcript_maker = TranscriptMaker(genome, annotation_updates[i], transcript_distributions[i], self.parameters)
            #    molecules.append(transcript_maker.prepare_transcripts())
            #r_rna = RibosomalRNA(self.parameters)
            #molecules.append(r_rna.generate_rRNA_sample())
        print("Execution of the Expression Pipeline Ended")
        #return molecules

    @staticmethod
    def main(configuration_file_path):
        with open(configuration_file_path, "r+") as configuration_file:
            configuration = json.load(configuration_file)
        seed = configuration.get('seed', GeneralUtils.generate_seed())
        np.random.seed(seed)
        pipeline = ExpressionPipeline(configuration)
        pipeline.validate()
        pipeline.execute()

class ExpressionPipelineException(Exception):
    pass


if __name__ == "__main__":
    sys.exit(ExpressionPipeline.main("../../config/config.json"))
