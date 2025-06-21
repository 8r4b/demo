import os
from pytube import YouTube

def download_youtube_video(url):
    downloads_folder = os.path.join(os.path.expanduser("~"), "Downloads")
    yt = YouTube(url)
    stream = yt.streams.get_highest_resolution()
    print(f"Downloading: {yt.title}")
    stream.download(output_path=downloads_folder)
    print(f"Downloaded to: {downloads_folder}")

if __name__ == "__main__":
    video_url = input("Enter YouTube video URL: ")
    download_youtube_video(video_url)