import pandas as pd
import plotly.graph_objs as go
import dash
import dash_core_components as dcc
import dash_html_components as html
import re

# Program to compare before and after molecule states for the Poly A Selection Step.
# Comparison based on log files provided by PolyAStep.py test.

app = dash.Dash()


def get_tail_length(sequence):
    """
    Obtain length of poly a tail from sequence
    :param sequence: sequence to examine - may not be poly-adenylated
    :return: length of poly a tail
    """
    match = re.search(r'(A+$)', sequence)
    return 0 if not match else len(match.group())


# Input data frame - adding seq length and tail length columns
input_df = pd.read_csv("../../data/polyA_step_input_data.log")
input_df['seq_length'] = input_df.apply(lambda row: len(row['sequence']), axis=1)
input_df['tail_length'] = input_df.apply(lambda row: get_tail_length(row['sequence']), axis=1)

# Output data frame - filtering out retained molecules and adding seq length and tail length columns
output_df = pd.read_csv("../../data/polyA_step_output_data.log")
retained_df = output_df[output_df["note"] != "removed"]
retained_df['seq_length'] = retained_df.apply(lambda row: len(row['sequence']), axis=1)
retained_df['tail_length'] = retained_df.apply(lambda row: get_tail_length(row['sequence']), axis=1)

# Histogram comparing sequence lengths before and after step
data_seq_lengths = [
    go.Histogram(
        x=retained_df['seq_length'],
        opacity=0.75,
        name="Retained Molecules"
    ),
    go.Histogram(
        x=input_df['seq_length'],
        opacity=0.75,
        name="Input Molecules"
    )
]

# Histogram comparing tail length before and after step
data_tail_lengths = [
    go.Histogram(
        x=retained_df['tail_length'],
        opacity=0.75,
        name="Retained Molecules"
    ),
    go.Histogram(
        x=input_df['tail_length'],
        opacity=0.75,
        name="Input Molecules"
    )
]

# Histogram displaying relative start positions after step.  Before step, position is assumed always 1.
data_start_positions = [
    go.Histogram(
        x=retained_df['start'],
        opacity=0.75,
        name="Retained Molecules"
    )
]

figure_seq_lengths = {
    'data': data_seq_lengths,
    'layout': go.Layout(
            barmode='overlay',
            title='Poly A Selection Step Data - Sequence Lengths',
            hovermode='closest')
    }

figure_tail_lengths = {
    'data': data_tail_lengths,
    'layout': go.Layout(
            barmode='overlay',
            title='Poly A Selection Step Data - Tail Lengths',
            hovermode='closest')
    }

figure_start_positions = {
    'data': data_start_positions,
    'layout': go.Layout(
            barmode='overlay',
            title='Poly A Selection Step Data - Relative Start Positions',
            hovermode='closest')
    }

app.layout = html.Div([
    dcc.Graph(id='seq_lengths', figure=figure_seq_lengths),
    dcc.Graph(id='tail_lengths', figure=figure_tail_lengths),
    dcc.Graph(id='start_positions', figure=figure_start_positions)
])


if __name__ == '__main__':
    app.run_server()