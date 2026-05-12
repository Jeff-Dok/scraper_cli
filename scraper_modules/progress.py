# scraper_modules/progress.py
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, BarColumn,
    TaskProgressColumn, TransferSpeedColumn, DownloadColumn,
    TimeRemainingColumn,
)


def make_progress() -> Progress:
    """Retourne un Progress rich préconfiguré pour afficher vitesse et ETA.

    TransferSpeedColumn et DownloadColumn attendent task.completed en bytes —
    utiliser progress.advance(task_id, len(chunk)) lors des téléchargements.
    Pour les tâches non-fichiers (barre globale, spinner URL), ces colonnes
    affichent simplement « ? » ce qui est visuellement acceptable.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TransferSpeedColumn(),
        DownloadColumn(),
        TimeRemainingColumn(),
        transient=True,
    )
