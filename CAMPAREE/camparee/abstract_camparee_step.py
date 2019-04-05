from abc import abstractmethod
from beers_utils.abstract_pipeline_step import AbstractPipelineStep

class AbstractCampareeStep(AbstractPipelineStep):
    """
    Abstract class defining the minimal methods required by a step in the CAMPAREE
    pipeline.
    """

    @abstractmethod
    def execute(self):
        """
        Entry point into the CAMPAREE step.
        """
        pass

    @abstractmethod
    def validate(self):
        """
        Checks validity of parameters used to instantiate the CAMPAREE step.

        Returns
        -------
        boolean
            True  - All parameters required to run this step were provided and
                    are within valid ranges.
            False - One or more of the paramters is missing or contains an invalid
                    value.
        """
        pass

    @abstractmethod
    def get_commandline_call(self):
        """
        Prepare command to execute the step from the command line, given all of
        the parameters used to call the execute() method.

        Parameters
        ----------
        The same or equivalent parameters given to the execute() method.

        Returns
        -------
        string
            Command to execute on the command line. It will perform the same
            operations as a call to execute() with the same parameters.

        """
        pass

    @abstractmethod
    def get_validation_attributes(self):
        """
        Prepare attributes required by the is_output_valid() method to validate
        output generated by executing this specific instance of the pipeline step
        (either through the command line call or the execute method).

        Returns
        -------
        dict
            Key-value pairings of attributes accepted by the is_output_valid()
            method.

        """
        pass

    @staticmethod
    @abstractmethod
    def is_output_valid(validation_attributes):
        """
        Check if output of this step, for a specific job/execution is correctly
        formed and valid, given the dictionary of valdiation attributes. Prepare
        these attributes for a given executing by calling the get_validation_attributes()
        method.

        Parameters
        ----------
        validation_attributes : dict
            Key-value pairings of attributes generated by the get_validation_attributes()
            method.

        Returns
        -------
        boolean
            True  - Output files for this step were created and are well formed.
            False - Output files for this steo do not exist or are missing data.
        """
        pass

    @staticmethod
    @abstractmethod
    def main():
        """
        Entry point into script. Allows script to be executed/submitted via the
        command line.
        """
        pass
